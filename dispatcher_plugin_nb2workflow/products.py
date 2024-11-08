from __future__ import annotations

import logging
import os
import json

from cdci_data_analysis.analysis.products import LightCurveProduct, BaseQueryProduct, ImageProduct, SpectrumProduct
from cdci_data_analysis.analysis.parameters import Parameter
from oda_api.data_products import NumpyDataProduct, ODAAstropyTable, BinaryProduct, PictureProduct
from .util import AstropyTableViewParser, with_hashable_dict
from oda_api.ontology_helper import Ontology
from io import StringIO
from functools import lru_cache  
from mimetypes import guess_extension
from magic import from_buffer as mime_from_buffer

logger = logging.getLogger(__name__)



# TODO: this should probably be defined in the main dispatcher code
class TableProduct(BaseQueryProduct):
    def __init__(self, name, table_data, file_dir = './', **kwargs):
        self.table_data = table_data
        fname = name if name.endswith('csv') else f"{name}.ecsv"
        super().__init__(name, file_name=fname, file_dir = file_dir, **kwargs)

    def encode(self):
        return self.table_data.encode()

    def write(self, file_name=None, overwrite=True, file_dir=None):
        if file_name:
            file_path = self.file_path.get_file_path(file_name=file_name, file_dir=file_dir)
        else:
            file_path = self.file_path.path
            
        self.table_data.write(file_path, overwrite=overwrite, format='ascii.ecsv')
        
class NB2WProduct:
    def __init__(self, 
                 encoded_data, 
                 data_product_type=BaseQueryProduct,
                 out_dir='./', 
                 name='nb2w', 
                 extra_metadata={}):

        # this constructor is only valid for NumpyDataProduct-based products
        
        self.name = name
        self.extra_metadata = extra_metadata 
        metadata = encoded_data.get('meta_data', {})
        self.out_dir = out_dir
        numpy_data_prod = NumpyDataProduct.decode(encoded_data) 
        
        if not numpy_data_prod.name:
            numpy_data_prod.name = self.name

        self.dispatcher_data_prod = data_product_type(
            name=self.name, 
            data=numpy_data_prod,
            meta_data=metadata,
            file_dir=out_dir,
            file_name = f"{self.name}.fits")
    
    
    def write(self):
        file_path = self.dispatcher_data_prod.file_path
        self.dispatcher_data_prod.write()
        self.file_path = file_path.path
    
    def get_html_draw(self):
        return {'image': {'div': '<br>No preview available', 'script': ''} }
    
    @classmethod 
    def _init_as_list(cls, encoded_data, *args, **kwargs):
        encoded_data = cls._dejsonify(encoded_data)
        
        if isinstance(encoded_data, list):
            return [cls(elem, *args, **kwargs) for elem in encoded_data]
        
        return [cls(encoded_data, *args, **kwargs)]

    @classmethod
    @with_hashable_dict
    @lru_cache
    def _prod_list_description_analyser(
        cls, 
        bk_descript_dict = {}, 
        ontology_path = None
        ) -> dict[str, tuple[type[NB2WProduct], str, dict]]:

        if ontology_path is not None:
            onto = Ontology(ontology_path)
            par_prod_class_dict = {getattr(x, 'type_key'): x for x in parameter_products_factory(onto)}
        else:
            onto = None
            par_prod_class_dict = {}

        mapping = {getattr(x, 'type_key'): x for x in cls.__subclasses__() if hasattr(x, 'type_key')}
        mapping.update(par_prod_class_dict)

        prod_classes_dict = {}
        for key in bk_descript_dict.keys():
            extra_kw = {}
            name = key

            owl_type = bk_descript_dict[key]['owl_type'] 
            cls_owl_type = owl_type
            extra_ttl = bk_descript_dict[key].get('extra_ttl')
            if extra_ttl == '\n': extra_ttl = None

            if extra_ttl:
                if onto is None:
                    logger.warning('Product description of %s contains extra_ttl, but no ontology is loaded. Ignoring extra_ttl.')
                else:
                    onto.parse_extra_triples(extra_ttl)

                    if owl_type not in mapping.keys():
                        prod_hierarchy = onto.get_product_hierarchy(owl_type)
                        for ot in prod_hierarchy:
                            if ot in mapping.keys():
                                cls_owl_type = ot
                                break
                        # there is always a last resort to init as this base class
                        # will work for NumpyDataProduct-based
                    
                    if cls_owl_type in par_prod_class_dict:
                        extra_kw.update({'extra_ttl': extra_ttl})
                    
                    extra_metadata = {}
                    for emt in ['label', 'description', 'group']:
                        em = onto.get_direct_annotation(owl_type, emt)
                        if em:
                            extra_metadata[emt] = em
                    extra_kw.update({'extra_metadata': extra_metadata})

            prod_classes_dict[key] = (mapping.get(cls_owl_type, cls), name, extra_kw)
        
        return prod_classes_dict


    @classmethod
    def prod_list_factory(cls, output_description_dict, output, out_dir = './', ontology_path = None):
        
        prod_list = []

        for key, val in cls._prod_list_description_analyser(bk_descript_dict=output_description_dict, 
                                                            ontology_path=ontology_path).items():
            try:
                prod_list.extend(val[0]._init_as_list(output[key],
                                                      out_dir=out_dir, 
                                                      name=val[1],
                                                      **val[2]))
            except Exception as e:
                logger.error('unable to construct %s product: %s from this: %s ', key, e, output[key])

        return prod_list

    @staticmethod
    def _dejsonify(encoded_data):
        if isinstance(encoded_data, str):
            try:
                return json.loads(encoded_data)
            except json.decoder.JSONDecodeError:
                pass
        return encoded_data

class NB2WParameterProduct(NB2WProduct):
    type_key = 'oda:WorkflowParameter'
    
    ontology_path = None
        
    def __init__(self, 
                 value, 
                 out_dir=None, 
                 name='paramdata',
                 extra_ttl=None,
                 extra_metadata={}):
        self.name = name
        self.extra_metadata = extra_metadata
        self.parameter_obj = Parameter.from_owl_uri(
            owl_uri=self.type_key,
            extra_ttl=extra_ttl,
            ontology_path=self.ontology_path,
            value=value,
            name=name)
    
    def write(self):
        pass
    
    def get_html_draw(self):
        return {'image': {'div': f'<br>value: {self.parameter_obj.value}<br>uri: {self.type_key}', 'script': ''} }

@lru_cache
def parameter_products_factory(ontology: Ontology):
    classes = []
    for term in ontology.get_parprod_terms():
        classes.append(type(f"{term.split('#')[-1]}Product", 
                            (NB2WParameterProduct,), 
                            {'type_key': term, 'ontology_object': ontology}))
    return classes
        

class NB2WBinaryProduct(NB2WProduct): 
    type_key = 'http://odahub.io/ontology#ODABinaryProduct'
    
    def __init__(self, 
                 encoded_data, 
                 out_dir='./', 
                 name='bindata', 
                 extra_metadata={}):
        self.out_dir = out_dir
        self.name = name
        self.extra_metadata = extra_metadata
        self.data_prod = BinaryProduct.decode(encoded_data)
        self.mime_type = mime_from_buffer(self.data_prod.bin_data, mime=True)
    
    def write(self):
        ext = guess_extension(self.mime_type, strict=False)
        if ext is None: ext = ''
        file_path = os.path.join(self.out_dir, f"{self.name}{ext}")
        self.data_prod.write_file(file_path)
        self.file_path = file_path
        

class NB2WTextProduct(NB2WProduct): 
    type_key = 'http://odahub.io/ontology#ODATextProduct'
    
    def __init__(self, 
                 text_data, 
                 out_dir='./', 
                 name='text', 
                 extra_metadata={}):
        self.out_dir = out_dir
        self.name = name
        self.extra_metadata = extra_metadata
        self.data_prod = str(text_data)
        
    def write(self):
        file_path = os.path.join(self.out_dir, self.name)
        with open(file_path, 'w') as fd:
            fd.write(self.data_prod)
        self.file_path = file_path
        
    def get_html_draw(self):
        return {'image': {'div': '<br>'+self.data_prod, 'script': ''} }


class NB2WProgressProduct(NB2WProduct):

    def __init__(self, progress_html_data, out_dir=None, name='progress'):
        self.out_dir = out_dir
        self.name = name
        self.progress_data = progress_html_data


class NB2WPictureProduct(NB2WProduct): 
    type_key = 'http://odahub.io/ontology#ODAPictureProduct'  
    
    def __init__(self, 
                 encoded_data, 
                 out_dir='./', 
                 name='picture', 
                 extra_metadata={}):
        self.name = name
        self.extra_metadata = extra_metadata
        self.out_dir = out_dir
        self.data_prod = PictureProduct.decode(encoded_data)
        if not self.data_prod.name:
            self.data_prod.name = self.name

    def write(self):
        file_path = os.path.join(self.out_dir, f"{self.name}.{self.data_prod.img_type}")
        self.data_prod.write_file(file_path)
        self.file_path = file_path

    def get_html_draw(self):
        enc = self.data_prod.encode()
        b64_dat = enc["b64data"].replace("-", "+").replace("_", "/") 
        return {'image': {'div': f'<br><img src="data:image/{enc["img_type"]};base64,{b64_dat}" class="img-responsive">', 
                          'script': ''} }

class NB2WAstropyTableProduct(NB2WProduct):
    type_key = 'http://odahub.io/ontology#ODAAstropyTable'
    
    def __init__(self, 
                 encoded_data, 
                 out_dir='./', 
                 name='astropy_table', 
                 extra_metadata={}):
        self.name = name
        self.extra_metadata = extra_metadata
        metadata = encoded_data.get('meta_data', {})
        self.out_dir = out_dir
        table_data_prod = ODAAstropyTable.decode(encoded_data)
        if not table_data_prod.name:
            table_data_prod.name = self.name
        self.dispatcher_data_prod = TableProduct(name = self.name, 
                                                 table_data = table_data_prod,
                                                 meta_data=metadata,
                                                 file_dir = out_dir)
    
    def get_html_draw(self):
        with StringIO() as sio:
            self.dispatcher_data_prod.table_data.write(sio, format='jsviewer')
            sio.seek(0)
            html_text = sio.read()
        
        parser = AstropyTableViewParser()
        parser.feed(html_text)
        
        script_text = parser.script.replace('$(document).ready', '').replace('$(', 'jQuery(').rpartition(');')[0] + ')();'
        
        return {'image': {'div': '<br><br>'+parser.tabcode,
                          'script': f"<script>{script_text}</script>"} }
        
class NB2WLightCurveProduct(NB2WProduct): 
    type_key = 'http://odahub.io/ontology#LightCurve'
        
    def __init__(self, 
                 encoded_data, 
                 out_dir=None, 
                 name='lc', 
                 extra_metadata={}):
        super().__init__(encoded_data, 
                         data_product_type=LightCurveProduct, 
                         out_dir='./', 
                         name=name, 
                         extra_metadata=extra_metadata)
        
    def get_html_draw(self):
        unit_ID=1 # TODO: it could be optional
        du = self.dispatcher_data_prod.data.data_unit[unit_ID]
        data, header, units = du.data, du.header, du.units_dict
        data_col = data.dtype.names[1]
        err_col = None
        if len(data.dtype.names) == 3 and data.dtype.names[2].startswith('ERR'):
            err_col = data.dtype.names[2]
        x_label = f"TIME, {units['TIME']}" if 'TIME' in units else 'TIME'
        y_units = getattr(units, data_col, None)
        y_label = f"{data_col}, {y_units}" if y_units else data_col 
        
        im_dic = self.dispatcher_data_prod.get_html_draw(x = data['TIME'], 
                                                         y=data[data_col], 
                                                         dy=data[err_col] if err_col else None,
                                                         x_label = x_label,
                                                         y_label = y_label
                                                         )
        return im_dic
   
class NB2WSpectrumProduct(NB2WProduct):
    type_key = 'http://odahub.io/ontology#Spectrum'
    
    def __init__(self, 
                 encoded_data, 
                 out_dir='./', 
                 name='spec', 
                 extra_metadata={}):
        super().__init__(encoded_data, 
                         data_product_type=SpectrumProduct, 
                         out_dir=out_dir, name=name, 
                         extra_metadata=extra_metadata)
        
class NB2WImageProduct(NB2WProduct):
    type_key = 'http://odahub.io/ontology#Image'
    
    def __init__(self, 
                 encoded_data, 
                 out_dir='./', 
                 name='image', 
                 extra_metadata={}):
        super().__init__(encoded_data, 
                         data_product_type=ImageProduct, 
                         out_dir=out_dir, 
                         name=name, 
                         extra_metadata=extra_metadata)

    def get_html_draw(self):
        return self.dispatcher_data_prod.get_html_draw()  # type: ignore