"""Provide analysis of input files by chromosomal regions.

Handle splitting and analysis of files from chromosomal subsets separated by
no-read regions.
"""
import collections
import os

from bcbio import utils
from bcbio.distributed.split import parallel_split_combine

def get_max_counts(samples):
    """Retrieve the maximum region size from a set of callable regions
    """
    bed_files = list(set(utils.get_in(x[0], ("config", "algorithm", "callable_regions"))
                         for x in samples))
    return max(sum(1 for line in open(f)) for f in bed_files if f)

# ## BAM preparation

def to_safestr(region):
    if region[0] in ["nochrom", "noanalysis"]:
        return region[0]
    else:
        return "_".join([str(x) for x in region])

# ## Split and delayed BAM combine

def _split_by_regions(dirname, out_ext, in_key):
    """Split a BAM file data analysis into chromosomal regions.
    """
    import pybedtools
    def _do_work(data):
        regions = [(r.chrom, int(r.start), int(r.stop))
                   for r in pybedtools.BedTool(data["config"]["algorithm"]["callable_regions"])]
        bam_file = data[in_key]
        if bam_file is None:
            return None, []
        part_info = []
        base_out = os.path.splitext(os.path.basename(bam_file))[0]
        nowork = [["nochrom"], ["noanalysis", data["config"]["algorithm"]["non_callable_regions"]]]
        for region in regions + nowork:
            out_dir = os.path.join(data["dirs"]["work"], dirname, data["name"][-1], region[0])
            region_outfile = os.path.join(out_dir, "%s-%s%s" %
                                          (base_out, to_safestr(region), out_ext))
            part_info.append((region, region_outfile))
        out_file = os.path.join(data["dirs"]["work"], dirname, data["name"][-1],
                                "%s%s" % (base_out, out_ext))
        return out_file, part_info
    return _do_work

def _add_combine_info(output, combine_map, file_key):
    """Do not actually combine, but add details for later combining work.

    Each sample will contain information on the out file and additional files
    to merge, enabling other splits and recombines without losing information.
    """
    files_per_output = collections.defaultdict(list)
    for part_file, out_file in combine_map.items():
        files_per_output[out_file].append(part_file)
    out_by_file = collections.defaultdict(list)
    out = []
    for data in output:
        # Do not pass along nochrom, noanalysis regions
        if data["region"][0] not in ["nochrom", "noanalysis"]:
            cur_file = data[file_key]
            # If we didn't process, no need to add combine information
            if cur_file in combine_map:
                out_file = combine_map[cur_file]
                if not "combine" in data:
                    data["combine"] = {}
                data["combine"][file_key] = {"out": out_file,
                                             "extras": files_per_output.get(out_file, [])}
                out_by_file[out_file].append(data)
            elif cur_file:
                out_by_file[cur_file].append(data)
            else:
                out.append([data])
    for samples in out_by_file.values():
        regions = [x["region"] for x in samples]
        region_bams = [x["work_bam"] for x in samples]
        assert len(regions) == len(region_bams)
        if len(set(region_bams)) == 1:
            region_bams = [region_bams[0]]
        data = samples[0]
        data["region_bams"] = region_bams
        data["region"] = regions
        out.append([data])
    return out

def parallel_prep_region(samples, run_parallel):
    """Perform full pre-variant calling BAM prep work on regions.
    """
    file_key = "work_bam"
    split_fn = _split_by_regions("bamprep", "-prep.bam", file_key)
    # identify samples that do not need preparation -- no recalibration or realignment
    extras = []
    torun = []
    for data in [x[0] for x in samples]:
        if data.get("work_bam"):
            data["align_bam"] = data["work_bam"]
        a = data["config"]["algorithm"]
        if (not a.get("recalibrate") and not a.get("realign") and not a.get("variantcaller", "gatk")):
            extras.append([data])
        elif not data.get(file_key):
            extras.append([data])
        else:
            torun.append([data])
    return extras + parallel_split_combine(torun, split_fn, run_parallel,
                                           "piped_bamprep", _add_combine_info, file_key, ["config"])

def delayed_bamprep_merge(samples, run_parallel):
    """Perform a delayed merge on regional prepared BAM files.
    """
    needs_merge = False
    for data in samples:
        if (data[0]["config"]["algorithm"].get("merge_bamprep", True) and
              "combine" in data[0]):
            needs_merge = True
            break
    if needs_merge:
        return run_parallel("delayed_bam_merge", samples)
    else:
        return samples

# ## Utilities

def clean_sample_data(samples):
    """Clean unnecessary information from sample data, reducing size for message passing.
    """
    out = []
    for data in (x[0] for x in samples):
        data["dirs"] = {"work": data["dirs"]["work"], "galaxy": data["dirs"]["galaxy"],
                        "fastq": data["dirs"].get("fastq")}
        data["config"] = {"algorithm": data["config"]["algorithm"],
                          "resources": data["config"]["resources"]}
        for remove_attr in ["config_file", "regions", "algorithm"]:
            data.pop(remove_attr, None)
        out.append([data])
    return out
