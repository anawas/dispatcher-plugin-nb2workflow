from cdci_data_analysis.analysis.queries import ProductQuery, QueryOutput, BaseQuery, InstrumentQuery
from cdci_data_analysis.analysis.parameters import Parameter, Name
from .products import (NB2WProduct,
                       NB2WAstropyTableProduct,
                       NB2WBinaryProduct,
                       NB2WPictureProduct,
                       NB2WTextProduct,
                       NB2WParameterProduct,
                       NB2WProgressProduct)
from cdci_data_analysis.analysis.ontology import Ontology
import os
from functools import lru_cache, wraps
from json import dumps
from copy import deepcopy

class HashableDict(dict):
    def __hash__(self):
        return hash(dumps(self, sort_keys=True))

def with_hashable_dict(func):
    @wraps(func)
    def wrapper(backend_param_dict, ontology_path):
        return func(HashableDict(backend_param_dict), ontology_path)
    return wrapper

@with_hashable_dict
@lru_cache
def construct_parameter_lists(backend_param_dict, ontology_path):
    src_query_pars_uris = { "http://odahub.io/ontology#PointOfInterestRA": "RA",
                            "http://odahub.io/ontology#PointOfInterestDEC": "DEC",
                            "http://odahub.io/ontology#StartTime": "T1",
                            "http://odahub.io/ontology#EndTime": "T2",
                            "http://odahub.io/ontology#AstrophysicalObject": "src_name",
                            "ThisNameShouldNotExist": "token",
                          }
    par_name_substitution = {'token': '_token'}
    # There are two name substitutions for "*token": 
    # 1) if backend notebook defines parameter named "token", it will be renamed by general mechanism (appending "_rename")
    # 2) the actual token (represented as SourceQuery parameter "token") will be sent to backend as "_token" request argument
    
    plist = []
    source_plist = []
    for pname, pval in backend_param_dict.items():
        onto = Ontology(ontology_path)
        if pval.get("extra_ttl"):
            onto.parse_extra_triples(pval.get("extra_ttl"), parse_oda_annotations = False)
        onto_class_hierarchy = onto.get_parameter_hierarchy(pval['owl_type'])
        src_query_owl_uri_set = set(onto_class_hierarchy).intersection(src_query_pars_uris.keys())
        if src_query_owl_uri_set:
            default_pname = src_query_pars_uris[src_query_owl_uri_set.pop()]
            par_name_substitution[ default_pname ] = pname
            source_plist.append(Parameter.from_owl_uri(pval['owl_type'],
                                                       value=pval['default_value'],
                                                       name=default_pname,
                                                       ontology_object=onto,
                                                       extra_ttl=pval.get("extra_ttl")
                                                       ))
        else:
            #if param name coincides with the SourceQuery default names, but it's not properly annotated, rename
            cur_name = pname
            if pname in src_query_pars_uris.values():
                cur_name = pname + '_rename'
                par_name_substitution[ cur_name ] = pname
            plist.append(Parameter.from_owl_uri(pval['owl_type'],
                                                value=pval['default_value'],
                                                name=cur_name,
                                                ontology_object=onto,
                                                extra_ttl=pval.get("extra_ttl")
                                                ))
    return {'source_plist': source_plist,
            'prod_plist': plist,
            'par_name_substitution': par_name_substitution
            }

class NB2WSourceQuery(BaseQuery):
    @classmethod
    def from_backend_options(cls, backend_options, ontology_path):
        product_names = backend_options.keys()
        # Note that different backend products could contain different sets of the source query parameters.
        # So we squash them into one list without duplicates
        parameters_dict = {}
        for product_name in product_names:
            backend_param_dict = backend_options[product_name]['parameters']
            prod_source_plist = deepcopy(construct_parameter_lists(backend_param_dict, ontology_path)['source_plist'])
            for par in prod_source_plist:
                parameters_dict[par.name] = par
        parameters_list = list(parameters_dict.values())
        try:
            token = Name(name_format='str', name='token', value=None, is_optional=True)
        except TypeError:
            # backward compatibility with dispatcher before "optional" parameters are introduced
            token = Name(name_format='str', name='token', value=None)
        parameters_list.append(token)
        return cls('src_query', parameters_list)


class NB2WProductQuery(ProductQuery):
    def __init__(self, name, backend_product_name, backend_param_dict, backend_output_dict, ontology_path=None):
        if ontology_path is None:
            # try to get the path from environemnt, else point to server
            ontology_path = os.environ.get('ODA_ONTOLOGY_PATH', "http://odahub.io/ontology/ontology.ttl")
        self.backend_product_name = backend_product_name
        self.backend_output_dict = backend_output_dict
        self.backend_param_dict = backend_param_dict
        parameter_lists = construct_parameter_lists(backend_param_dict, ontology_path)
        self.par_name_substitution = parameter_lists['par_name_substitution']
        plist = deepcopy(parameter_lists['prod_plist'])
        self.ontology_path = ontology_path
        super().__init__(name, parameters_list = plist)

    @classmethod
    def query_list_and_dict_factory(cls, backend_options, ontology_path):
        product_names = backend_options.keys()
        qlist = []
        qdict = {}
        for product_name in product_names:
            backend_param_dict = backend_options[product_name]['parameters']
            backend_output_dict = backend_options[product_name]['output']
            qlist.append(cls(f'{product_name}_query', product_name, backend_param_dict, backend_output_dict, ontology_path))
            qdict[product_name] = f'{product_name}_query'
        return qlist, qdict


    def get_data_server_query(self, instrument, config=None, **kwargs):
        param_dict = {}
        for param_name in instrument.get_parameters_name_list(prod_name = self.backend_product_name):
            param_instance = instrument.get_par_by_name(param_name, prod_name = self.backend_product_name)
            bk_pname = self.par_name_substitution.get(param_name, param_name)
            if bk_pname.startswith('_') or bk_pname in self.backend_param_dict:
                # should not send a source parameter if it's not in notebook
                param_dict[bk_pname] = param_instance.get_default_value()

        return instrument.data_server_query_class(instrument=instrument,
                                                config=config,
                                                param_dict=param_dict,
                                                task=self.backend_product_name)

    def build_product_list(self, instrument, res, out_dir, api=False):
        prod_list = []
        _output = None
        if out_dir is None:
            out_dir = './'
        res_progress_product = False
        # In the case of a dispatcher request where the progress of the execution has been requested
        # (`return_progress: True`), the get_progress_run wraps the response from the nb2service within a dict,
        # so that it is easier here to understand how to treat the response, and build the correct product list.
        # In case of a standard request then the res argument is expected to be a Response object with the content in
        # json format.
        if isinstance(res, dict):
            res_progress_product = res.get('progress_product', False)
            res = res.get('res', None)
        if res is not None:
            res_content_type = res.headers.get('content-type', None)
            if res_content_type is not None and res_content_type == 'application/json':
                if 'output' in res.json().keys(): # in synchronous mode
                    _o_dict = res.json()
                else:
                    _o_dict = res.json()['data']
                _output = _o_dict['output']
                prod_list = NB2WProduct.prod_list_factory(self.backend_output_dict, _output, out_dir, self.ontology_path)
            else:
                _o_text = res.content.decode()
                if res_progress_product:
                    prod_list.append(NB2WProgressProduct(_o_text, out_dir))

        return prod_list

    def process_product_method(self, instrument, prod_list, api=False):
        query_out = QueryOutput()


        np_dp_list, bin_dp_list, tab_dp_list, bin_im_dp_list, text_dp_list, progress_dp_list = [], [], [], [], [], []
        if api is True:
            for product in prod_list.prod_list:
                if isinstance(product, NB2WAstropyTableProduct):
                    tab_dp_list.append(product.dispatcher_data_prod.table_data)
                elif isinstance(product, NB2WBinaryProduct):
                    bin_dp_list.append(product.data_prod)
                elif isinstance(product, NB2WPictureProduct):
                    bin_im_dp_list.append(product.data_prod)
                elif isinstance(product, NB2WTextProduct):
                    text_dp_list.append({'name': product.name, 'value': product.data_prod})
                elif isinstance(product, NB2WParameterProduct):
                    text_dp_list.append({'name': product.name,
                                         'value': product.parameter_obj.value,
                                         'meta_data': {'uri': product.type_key}})
                elif isinstance(product, NB2WProgressProduct):
                    progress_dp_list.append({'name': product.name,
                                             'value': product.progress_data})
                else: # NB2WProduct contains NumpyDataProd by default
                    np_dp_list.append(product.dispatcher_data_prod.data)

            query_out.prod_dictionary['numpy_data_product_list'] = np_dp_list
            query_out.prod_dictionary['astropy_table_product_ascii_list'] = tab_dp_list
            query_out.prod_dictionary['binary_data_product_list'] = bin_dp_list
            query_out.prod_dictionary['binary_image_product_list'] = bin_im_dp_list
            query_out.prod_dictionary['text_product_list'] = text_dp_list
            query_out.prod_dictionary['progress_product_list'] = progress_dp_list
        else:
            prod_name_list, file_name_list, image_list, progress_product_list = [], [], [], []
            for product in prod_list.prod_list:
                if not isinstance(product, NB2WProgressProduct):
                    html_draw = product.get_html_draw()
                    product.write()
                    try:
                        file_name_list.append(os.path.basename(product.file_path))
                    except AttributeError:
                        pass
                    if html_draw:
                        image_list.append(html_draw)
                else:
                    html_draw = product.progress_data
                    progress_product_list.append(html_draw)

                prod_name_list.append(product.name)

            query_out.prod_dictionary['file_name'] = file_name_list
            query_out.prod_dictionary['image'] = image_list[0] if len(image_list) == 1 else image_list
            query_out.prod_dictionary['name'] = prod_name_list
            if len(prod_list.prod_list) == 1 and isinstance(prod_list.prod_list[0], NB2WProgressProduct):
                query_out.prod_dictionary['progress_product_html_output'] = progress_product_list
            else:
                if len(file_name_list) == 1:
                    query_out.prod_dictionary['download_file_name'] = f'{file_name_list[0]}.gz'
                else:
                    query_out.prod_dictionary['download_file_name'] = f'{self.backend_product_name}.tar.gz'
            query_out.prod_dictionary['prod_process_message'] = ''

        return query_out

class NB2WInstrumentQuery(InstrumentQuery):
    def __init__(self, name, restricted_access):
        super().__init__(name, restricted_access=restricted_access)
        self._parameters_list = []
        self._build_par_dictionary()

