"""Functions for retrieving metadata from MetaboLights.

This module connects to the European Bioinformatics Institute's
MetaboLights database. If you have problems with it, check that
it's working at http://www.ebi.ac.uk/metabolights/
"""
from __future__ import absolute_import
import ftplib
import glob
import logging
import os
import pandas as pd
import tempfile
import shutil
import re

from isatools import config
from isatools import isatab
from isatools.convert import isatab2json
from isatools.model import OntologyAnnotation


EBI_FTP_SERVER = 'ftp.ebi.ac.uk'
MTBLS_BASE_DIR = '/pub/databases/metabolights/studies/public'
INVESTIGATION_FILENAME = 'i_Investigation.txt'

logging.basicConfig(level=config.log_level)
log = logging.getLogger(__name__)

# REGEXES
_RX_FACTOR_VALUE = re.compile('Factor Value\[(.*?)\]')


def get(mtbls_study_id, target_dir=None):
    """
    This function downloads ISA content from the MetaboLights FTP site.

    :param mtbls_study_id: Study identifier for MetaboLights study to get, as a str (e.g. MTBLS1)
    :param target_dir: Path to write files to. If None, writes to temporary directory (generated on the fly)
    :return: Path where the files were written to

    Example usage:
        isa_json = MTBLS.get_study('MTBLS1', '/tmp/mtbls')
    """
    logging.info("Setting up ftp with {}".format(EBI_FTP_SERVER))
    ftp = ftplib.FTP(EBI_FTP_SERVER)
    logging.info("Logging in as anonymous user...")
    response = ftp.login()
    if '230' in response:  # 230 means Login successful
        logging.info("Log in successful!")
        try:
            logging.info("Looking for study '{}'".format(mtbls_study_id))
            ftp.cwd('{base_dir}/{study}'.format(base_dir=MTBLS_BASE_DIR, study=mtbls_study_id))
            if target_dir is None:
                target_dir = tempfile.mkdtemp()
            logging.info("Using directory '{}'".format(target_dir))
            with open(os.path.join(target_dir, INVESTIGATION_FILENAME), 'wb') as out_file:
                logging.info("Retrieving file '{}'".format(EBI_FTP_SERVER + MTBLS_BASE_DIR + '/' + mtbls_study_id + '/' + INVESTIGATION_FILENAME))
                ftp.retrbinary('RETR ' + INVESTIGATION_FILENAME, out_file.write)
            with open(out_file.name, encoding='utf-8') as i_fp:
                i_bytes = i_fp.read()
                lines = i_bytes.splitlines()
                s_filenames = [l.split('\t')[1][1:-1] for l in lines if 'Study File Name' in l]
                for s_filename in s_filenames:
                    with open(os.path.join(target_dir, s_filename), 'wb') as out_file:
                        logging.info("Retrieving file '{}'".format(
                            EBI_FTP_SERVER + MTBLS_BASE_DIR + '/' + mtbls_study_id + '/' + s_filename))
                        ftp.retrbinary('RETR ' + s_filename, out_file.write)
                a_filenames_lines = [l.split('\t') for l in lines if 'Study Assay File Name' in l]
                for a_filename_line in a_filenames_lines:
                    for a_filename in [f[1:-1] for f in a_filename_line[1:]]:
                        with open(os.path.join(target_dir, a_filename), 'wb') as out_file:
                            logging.info("Retrieving file '{}'".format(
                                EBI_FTP_SERVER + MTBLS_BASE_DIR + '/' + mtbls_study_id + '/' + a_filename))
                            ftp.retrbinary('RETR ' + a_filename, out_file.write)
        except ftplib.error_perm as ftperr:
            log.fatal("Could not retrieve MetaboLights study '{study}': {error}".format(study=mtbls_study_id, error=ftperr))
        finally:
            ftp.close()
            return target_dir
    else:
        ftp.close()
        raise ConnectionError("There was a problem connecting to MetaboLights: " + response)


def getj(mtbls_study_id):
    """
    This function downloads the specified MetaboLights study and returns an ISA JSON representation of it

    :param mtbls_study_id: Study identifier for MetaboLights study to get, as a str (e.g. MTBLS1)
    :return: ISA JSON representation of the MetaboLights study

    Example usage:
        isa_json = MTBLS.load('MTBLS1')
    """
    tmp_dir = get(mtbls_study_id)
    if tmp_dir is None:
        raise IOError("There was a problem retrieving the study ", mtbls_study_id)
    isa_json = isatab2json.convert(tmp_dir, identifier_type=isatab2json.IdentifierType.name,
                                   validate_first=False,
                                   use_new_parser=True)
    shutil.rmtree(tmp_dir)
    return isa_json


def get_data_files(mtbls_study_id, factor_selection=None):
    tmp_dir = get(mtbls_study_id)
    if tmp_dir is None:
        raise IOError("There was a problem retrieving study {}. Does it exist?".format(mtbls_study_id))
    else:
        result = slice_data_files(tmp_dir, factor_selection=factor_selection)
    shutil.rmtree(tmp_dir)
    return result


def slice_data_files(dir, factor_selection=None):
    """
    This function gets a list of samples and related data file URLs for a given MetaboLights study, optionally
    filtered by factor value (currently by matching on exactly 1 factor value)

    :param mtbls_study_id: Study identifier for MetaboLights study to get, as a str (e.g. MTBLS1)
    :param factor_selection: A list of selected factor values to filter on samples
    :return: A list of dicts {sample_name, list of data_files} containing sample names with associated data filenames

    Example usage:
        samples_and_data = mtbls.get_data_files('MTBLS1', [{'Gender': 'Male'}])

    TODO:  Need to work on more complex filters e.g.:
        {"gender": ["male", "female"]} selects samples matching "male" or "female" factor value
        {"age": {"equals": 60}} selects samples matching age 60
        {"age": {"less_than": 60}} selects samples matching age less than 60
        {"age": {"more_than": 60}} selects samples matching age more than 60

        To select samples matching "male" and age less than 60:
        {
            "gender": "male",
            "age": {
                "less_than": 60
            }
        }
    """
    results = list()
    # first collect matching samples
    for table_file in glob.iglob(os.path.join(dir, '[a|s]_*')):
        log.info("Loading {}".format(table_file))
        with open(table_file, encoding='utf-8') as fp:
            df = isatab.load_table(fp)
            if factor_selection is None:
                matches = df['Sample Name'].items()
                for indx, match in matches:
                    sample_name = match
                    if len([r for r in results if r['sample'] == sample_name]) == 1:
                        continue
                    else:
                        results.append(
                            {
                                "sample": sample_name,
                                "data_files": []
                            }
                        )
            else:
                for factor_name, factor_value in factor_selection.items():
                    if 'Factor Value[{}]'.format(factor_name) in list(df.columns.values):
                        matches = df.loc[df['Factor Value[{}]'.format(factor_name)] == factor_value]['Sample Name'].items()
                        for indx, match in matches:
                            sample_name = match
                            if len([r for r in results if r['sample'] == sample_name]) == 1:
                                continue
                            else:
                                results.append(
                                    {
                                        "sample": sample_name,
                                        "data_files": [],
                                        "query_used": factor_selection
                                    }
                                )
    # now collect the data files relating to the samples
    for result in results:
        sample_name = result['sample']
        for table_file in glob.iglob(os.path.join(dir, 'a_*')):
            with open(table_file, encoding='utf-8') as fp:
                df = isatab.load_table(fp)
                data_files = list()
                table_headers = list(df.columns.values)
                sample_rows = df.loc[df['Sample Name'] == sample_name]
                if 'Raw Spectral Data File' in table_headers:
                    data_files = sample_rows['Raw Spectral Data File']
                elif 'Free Induction Decay Data File' in table_headers:
                    data_files = sample_rows['Free Induction Decay Data File']
                result['data_files'] = [i for i in list(data_files) if str(i) != 'nan']
    return results


def get_factor_names(mtbls_study_id):
    """
    This function gets the factor names used in a MetaboLights study

    :param mtbls_study_id: Accession number of the MetaboLights study
    :return: A set of factor names used in the study

    Example usage:
        factor_names = get_factor_names('MTBLS1')
    """
    tmp_dir = get(mtbls_study_id)
    factors = set()
    for table_file in glob.iglob(os.path.join(tmp_dir, '[a|s]_*')):
        with open(os.path.join(tmp_dir, table_file), encoding='utf-8') as fp:
            df = isatab.load_table(fp)
            factors_headers = [header for header in list(df.columns.values) if _RX_FACTOR_VALUE.match(header)]
            for header in factors_headers:
                factors.add(header[13:-1])
    return factors


def get_factor_values(mtbls_study_id, factor_name):
    """
    This function gets the factor values of a factor in a MetaboLights study

    :param mtbls_study_id: Accession number of the MetaboLights study
    :param factor_name: The factor name for which values are being queried
    :return: A set of factor values associated with the factor and study

    Example usage:
        factor_values = get_factor_values('MTBLS1', 'genotype')
    """
    tmp_dir = get(mtbls_study_id)
    fvs = set()
    for table_file in glob.iglob(os.path.join(tmp_dir, '[a|s]_*')):
        with open(os.path.join(tmp_dir, table_file), encoding='utf-8') as fp:
            df = isatab.load_table(fp)
            if 'Factor Value[{}]'.format(factor_name) in list(df.columns.values):
                for _, match in df['Factor Value[{}]'.format(factor_name)].iteritems():
                    try:
                        match = match.item()
                    except AttributeError:
                        pass
                    if isinstance(match, (str, int, float)):
                        if str(match) != 'nan':
                            fvs.add(match)
    shutil.rmtree(tmp_dir)
    return fvs


def load(mtbls_study_id):
    tmp_dir = get(mtbls_study_id)
    if tmp_dir is None:
        raise IOError("There was a problem retrieving the study ", mtbls_study_id)
    with open(glob.glob(os.path.join(tmp_dir, 'i_*.txt'))[0], encoding='utf-8') as f:
        ISA = isatab.load(f)
        shutil.rmtree(tmp_dir)
        return ISA


def get_factors_summary(mtbls_study_id):
    """
    This function generates a factors summary for a MetaboLights study

    :param mtbls_study_id: Accession number of the MetaboLights study
    :return: A list of dicts summarising the set of factor names and values associated with each sample

    Note: it only returns a summary of factors with variable values.

    Example usage:
        factor_summary = get_factors_summary('MTBLS1')
        [
            {
                "name": "ADG19007u_357", 
                "Metabolic syndrome": "Control Group", 
                "Gender": "Female"
            }, 
            {
                "name": "ADG10003u_162", 
                "Metabolic syndrome": "diabetes mellitus",
                "Gender": "Female"
            },
        ]


    """
    ISA = load(mtbls_study_id=mtbls_study_id)
    all_samples = []
    for study in ISA.studies:
        all_samples.extend(study.samples)
    samples_and_fvs = []
    for sample in all_samples:
        sample_and_fvs = {
                "sources": ';'.join([x.name for x in sample.derives_from]),
                "sample": sample.name,
            }
        for fv in sample.factor_values:
            if isinstance(fv.value, (str, int, float)):
                fv_value = fv.value
            elif isinstance(fv.value, OntologyAnnotation):
                fv_value = fv.value.term
            sample_and_fvs[fv.factor_name.name] = fv_value
        samples_and_fvs.append(sample_and_fvs)
    df = pd.DataFrame(samples_and_fvs)
    nunique = df.apply(pd.Series.nunique)
    cols_to_drop = nunique[nunique == 1].index
    df = df.drop(cols_to_drop, axis=1)
    return df.to_dict(orient='records')


def get_study_groups(mtbls_study_id):
    factors_summary = get_factors_summary(mtbls_study_id=mtbls_study_id)
    study_groups = {}
    for factors_item in factors_summary:
        fvs = tuple(factors_item[k] for k in factors_item.keys() if k != 'name')
        if fvs in study_groups.keys():
            study_groups[fvs].append(factors_item['name'])
        else:
            study_groups[fvs] = [factors_item['name']]
    return study_groups


def get_study_groups_samples_sizes(mtbls_study_id):
    study_groups = get_study_groups(mtbls_study_id=mtbls_study_id)
    return list(map(lambda x: (x[0], len(x[1])), study_groups.items()))


def get_sources_for_sample(mtbls_study_id, sample_name):
    ISA = load(mtbls_study_id=mtbls_study_id)
    hits = []
    for study in ISA.studies:
        for sample in study.samples:
            if sample.name == sample_name:
                print('found a hit ', sample.name)
                for source in sample.derives_from:
                    hits.append(source.name)
    return hits


def get_data_for_sample(mtbls_study_id, sample_name):
    ISA = load(mtbls_study_id=mtbls_study_id)
    hits = []
    for study in ISA.studies:
        for assay in study.assays:
            for data in assay.data_files:
                if data.generated_from.name == sample_name:
                    print('found a hit ', data.filename)
    return hits


def get_study_groups_data_sizes(mtbls_study_id):
    study_groups = get_study_groups(mtbls_study_id=mtbls_study_id)
    return list(map(lambda x: (x[0], len(x[1])), study_groups.items()))


def get_characteristics_summary(mtbls_study_id):
    """
        This function generates a characteristics summary for a MetaboLights study

        :param mtbls_study_id: Accession number of the MetaboLights study
        :return: A list of dicts summarising the set of characteristic names and values associated with each sample

        Note: it only returns a summary of characteristics with variable values.

        Example usage:
            characteristics_summary = get_characteristics_summary('MTBLS5')
            [
                {
                    "name": "6089if_9",
                    "Variant": "Synechocystis sp. PCC 6803.sll0171.ko"
                },
                {
                    "name": "6089if_43",
                    "Variant": "Synechocystis sp. PCC 6803.WT.none"
                },
            ]


        """
    ISA = load(mtbls_study_id=mtbls_study_id)
    all_samples = []
    for study in ISA.studies:
        all_samples.extend(study.samples)
    samples_and_characs = []
    for sample in all_samples:
        sample_and_characs = {
                "name": sample.name
            }
        for source in sample.derives_from:
            for c in source.characteristics:
                if isinstance(c.value, (str, int, float)):
                    c_value = c.value
                elif isinstance(c.value, OntologyAnnotation):
                    c_value = c.value.term
                sample_and_characs[c.category.term] = c_value
        samples_and_characs.append(sample_and_characs)
    df = pd.DataFrame(samples_and_characs)
    nunique = df.apply(pd.Series.nunique)
    cols_to_drop = nunique[nunique == 1].index
    df = df.drop(cols_to_drop, axis=1)
    return df.to_dict(orient='records')


# PVs don't seem to vary in MTBLS, so maybe skip this function
# def get_parameter_value_summary(mtbls_study_id):
#     """
#         This function generates a parameter values summary for a MetaboLights study
#
#         :param mtbls_study_id: Accession number of the MetaboLights study
#         :return: A list of dicts summarising the set of parameters and values associated with each sample
#
#         Note: it only returns a summary of parameter values with variable values.
#
#         """
#     ISA = load(mtbls_study_id=mtbls_study_id)
#     all_samples = []
#     for study in ISA.studies:
#         all_samples.extend(study.samples)
#     samples_and_pvs = []
#     for sample in all_samples:
#         sample_and_pvs = {
#             "name": sample.name
#         }
#         for study in ISA.studies:
#             s_processes_linked_to_sample = [x for x in nx.algorithms.ancestors(study.graph, sample) if
#                                             isinstance(x, Process)]
#             for process in s_processes_linked_to_sample:
#                 for pv in process.parameter_values:
#                     if isinstance(pv, ParameterValue):
#                         if isinstance(pv.value, (str, int, float)):
#                             pv_value = pv.value
#                         elif isinstance(pv.value, OntologyAnnotation):
#                             pv_value = pv.value.term
#                         sample_and_pvs[pv.category.parameter_name.term] = pv_value
#             for assay in study.assays:
#                 for sample in assay.samples:
#                     a_processes_linked_to_sample = [x for x in nx.algorithms.descendants(assay.graph, sample) if
#                                                     isinstance(x, Process)]
#                     for process in a_processes_linked_to_sample:
#                         for pv in process.parameter_values:
#                             if isinstance(pv, ParameterValue):
#                                 if isinstance(pv.value, (str, int, float)):
#                                     pv_value = pv.value
#                                 elif isinstance(pv.value, OntologyAnnotation):
#                                     pv_value = pv.value.term
#                                 sample_and_pvs[pv.category.parameter_name.term] = pv_value
#         samples_and_pvs.append(sample_and_pvs)
#     df = pd.DataFrame(samples_and_pvs)
#     nunique = df.apply(pd.Series.nunique)
#     cols_to_drop = nunique[nunique == 1].index
#     df = df.drop(cols_to_drop, axis=1)
#     return df.to_dict(orient='records')


def get_study_variable_summary(mtbls_study_id):
    ISA = load(mtbls_study_id=mtbls_study_id)
    all_samples = []
    for study in ISA.studies:
        all_samples.extend(study.samples)
    samples_and_variables = []
    for sample in all_samples:
        sample_and_vars = {
            "sample_name": sample.name
        }
        for fv in sample.factor_values:
            if isinstance(fv.value, (str, int, float)):
                fv_value = fv.value
            elif isinstance(fv.value, OntologyAnnotation):
                fv_value = fv.value.term
            sample_and_vars[fv.factor_name.name] = fv_value
        for source in sample.derives_from:
            sample_and_vars["source_name"] = source.name
            for c in source.characteristics:
                if isinstance(c.value, (str, int, float)):
                    c_value = c.value
                elif isinstance(c.value, OntologyAnnotation):
                    c_value = c.value.term
                sample_and_vars[c.category.term] = c_value
        # Don't think pvs vary, so maybe skip this section
        # for study in ISA.studies:
        #     s_processes_linked_to_sample = [x for x in nx.algorithms.ancestors(study.graph, sample) if
        #                                     isinstance(x, Process)]
        #     for process in s_processes_linked_to_sample:
        #         for pv in process.parameter_values:
        #             if isinstance(pv, ParameterValue):
        #                 if isinstance(pv.value, (str, int, float)):
        #                     pv_value = pv.value
        #                 elif isinstance(pv.value, OntologyAnnotation):
        #                     pv_value = pv.value.term
        #                 sample_and_vars[pv.category.parameter_name.term] = pv_value
        #     for assay in study.assays:
        #         for sample in assay.samples:
        #             a_processes_linked_to_sample = [x for x in nx.algorithms.descendants(assay.graph, sample) if
        #                                             isinstance(x, Process)]
        #             for process in a_processes_linked_to_sample:
        #                 for pv in process.parameter_values:
        #                     if isinstance(pv, ParameterValue):
        #                         if isinstance(pv.value, (str, int, float)):
        #                             pv_value = pv.value
        #                         elif isinstance(pv.value, OntologyAnnotation):
        #                             pv_value = pv.value.term
        #                         sample_and_vars[pv.category.parameter_name.term] = pv_value
        samples_and_variables.append(sample_and_vars)
    df = pd.DataFrame(samples_and_variables)
    nunique = df.apply(pd.Series.nunique)
    cols_to_drop = nunique[nunique == 1].index
    df = df.drop(cols_to_drop, axis=1)
    return df.to_dict(orient='records')


def get_study_group_factors(mtbls_study_id):
    factors_list = []
    tmp_dir = get(mtbls_study_id)
    if tmp_dir is None:
        raise FileNotFoundError("Could not download {}".format(mtbls_study_id))
    for table_file in glob.iglob(os.path.join(tmp_dir, '[a|s]_*')):
        with open(os.path.join(tmp_dir, table_file), encoding='utf-8') as fp:
            df = isatab.load_table(fp)
            factor_columns = [x for x in df.columns if x.startswith("Factor Value")]
            if len(factor_columns) > 0:
                factors_list = df[factor_columns].drop_duplicates()\
                    .to_dict(orient='records')
    return factors_list


def get_filtered_df_on_factors_list(mtbls_study_id):
    factors_list = get_study_group_factors(mtbls_study_id=mtbls_study_id)
    queries = []
    for item in factors_list:
        query_str = []
        for k, v in item.items():
            k = k.replace(' ', '_').replace('[', '_').replace(']', '_')
            if isinstance(v, str):
                v = v.replace(' ', '_').replace('[', '_').replace(']', '_')
                query_str.append("{0} == '{1}' and ".format(k, v))
        query_str = ''.join(query_str)[:-4]
        queries.append(query_str)
    tmp_dir = get(mtbls_study_id)
    for table_file in glob.iglob(os.path.join(tmp_dir, '[a|s]_*')):
        with open(os.path.join(tmp_dir, table_file), encoding='utf-8') as fp:
            df = isatab.load_table(fp)
            cols = df.columns
            cols = cols.map(lambda x: x.replace(' ', '_') if isinstance(x, str) else x)
            df.columns = cols
            cols = df.columns
            cols = cols.map(lambda x: x.replace('[', '_') if isinstance(x, str) else x)
            df.columns = cols
            cols = df.columns
            cols = cols.map(lambda x: x.replace(']', '_') if isinstance(x, str) else x)
            df.columns = cols
        from pandas.computation.ops import UndefinedVariableError
        for query in queries:
            try:
                df2 = df.query(query)  # query uses pandas.eval, which evaluates queries like pure Python notation
                if "Sample_Name" in df.columns:
                    print("Group: {} / Sample_Name: {}".format(query, list(df2["Sample_Name"])))
                if "Source_Name" in df.columns:
                    print("Group: {} / Sources_Name: {}".format(query, list(df2["Source_Name"])))
                if "Raw_Spectral_Data_File" in df.columns:
                    print("Group: {} / Raw_Spectral_Data_File: {}".format(query[13:-2],
                                                                          list(df2["Raw_Spectral_Data_File"])))
            except UndefinedVariableError:
                pass
    return queries


def get_mtbls_list():
    logging.info("Setting up ftp with {}".format(EBI_FTP_SERVER))
    ftp = ftplib.FTP(EBI_FTP_SERVER)
    logging.info("Logging in as anonymous user...")
    response = ftp.login()
    mtbls_list = []
    if '230' in response:  # 230 means Login successful
        logging.info("Log in successful!")
        try:
            ftp.cwd('{base_dir}'.format(base_dir=MTBLS_BASE_DIR))
            mtbls_list = ftp.nlst()
        except ftplib.error_perm as ftperr:
            log.error("Could not get MTBLS directory list. Error: {}".format(ftperr))
    return mtbls_list


def dl_all_mtbls_isatab(target_dir):
    download_count = 0
    for i, mtblsid in enumerate(get_mtbls_list()):
        target_mtbls_subdir = os.path.join(target_dir, mtblsid)
        if not os.path.exists(target_mtbls_subdir):
            os.makedirs(target_mtbls_subdir)
        get(mtblsid, target_mtbls_subdir)
        download_count = i
    print("Downloaded {} ISA-Tab studies from MetaboLights".format(download_count))