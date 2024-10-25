import logging
import os
import json

from cdci_data_analysis.analysis.products import LightCurveProduct, BaseQueryProduct, ImageProduct, SpectrumProduct
from cdci_data_analysis.analysis.parameters import Parameter, subclasses_recursive
from oda_api.data_products import NumpyDataProduct, ODAAstropyTable, BinaryProduct, PictureProduct
from .util import AstropyTableViewParser
from cdci_data_analysis.analysis.ontology import Ontology
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
        self.table_data.encode()

    def write(self, file_name=None, overwrite=True, file_dir=None):
        if file_name:
            file_path = self.file_path.get_file_path(file_name=file_name, file_dir=file_dir)
        else:
            file_path = self.file_path.path
            
        self.table_data.write(file_path, overwrite=overwrite, format='ascii.ecsv')
        
class NB2WProduct:  # TODO: decide on the name precedence 
    def __init__(self, encoded_data, data_product_type = BaseQueryProduct, out_dir = None, name = 'nb2w'):
        self.name = name
        metadata = encoded_data.get('meta_data', {})
        self.out_dir = out_dir
        numpy_data_prod = NumpyDataProduct.decode(encoded_data) # most products are NumpyDataProduct so default: 
                                                                # we follow BaseQueryProduct implementation
        if not numpy_data_prod.name:
            numpy_data_prod.name = self.name
        else:
            self.name = numpy_data_prod.name
        self.dispatcher_data_prod = data_product_type(name = self.name, 
                                                     data= numpy_data_prod,
                                                     meta_data=metadata,
                                                     file_dir = out_dir,
                                                     file_name = f"{self.name}.fits")
    
    def write(self):
        file_path = self.dispatcher_data_prod.file_path
        self.dispatcher_data_prod.write()
        self.file_path = file_path.path
    
    def get_html_draw(self):
        try:
            return self.dispatcher_data_prod.get_html_draw()
        except:
            return {'image': {'div': '<br>No preview available', 'script': ''} }
        
    
    @classmethod 
    def _init_as_list(cls, encoded_data, *args, **kwargs):
        encoded_data = cls._dejsonify(encoded_data)
        
        if isinstance(encoded_data, list):
            return [cls(elem, *args, **kwargs) for elem in encoded_data]
        
        return [cls(encoded_data, *args, **kwargs)]

    @classmethod
    def prod_list_factory(cls, output_description_dict, output, out_dir = None, ontology_path = None):
        par_prod_classes = parameter_products_factory(ontology_path)

        mapping = {x.type_key: x for x in cls.__subclasses__() + par_prod_classes if hasattr(x, 'type_key')}
        
        prod_list = []
        for key in output_description_dict.keys():
            owl_type = output_description_dict[key]['owl_type']

            extra_kw = {}
            extra_ttl = output_description_dict[key].get('extra_ttl')
            if extra_ttl == '\n': extra_ttl = None
            if extra_ttl:
                extra_kw = {'extra_ttl': extra_ttl}

            try:
                prod_list.extend( mapping.get(owl_type, cls)._init_as_list(output[key],
                                                                           out_dir=out_dir,
                                                                           name=key,
                                                                           **extra_kw
                                                                           )
                                 )
            except Exception as e:
                logger.warning('unable to construct %s product: %s from this: %s ', key, e, output[key])

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
                 extra_ttl=None):
        self.name = name
        self.parameter_obj = Parameter.from_owl_uri(owl_uri=self.type_key,
                                                    extra_ttl=extra_ttl,
                                                    ontology_path=self.ontology_path,
                                                    value=value,
                                                    name=name)
    
    def write(self):
        pass
    
    def get_html_draw(self):
        return {'image': {'div': f'<br>value: {self.parameter_obj.value}<br>uri: {self.type_key}', 'script': ''} }

@lru_cache
def parameter_products_factory(ontology_path = None):
    classes = []
    onto = Ontology(ontology_path)
    for term in onto.get_parprod_terms():
        classes.append(type(f"{term.split('#')[-1]}Product", 
                            (NB2WParameterProduct,), 
                            {'type_key': term, 'ontology_object': onto}))
    return classes
        

class NB2WBinaryProduct(NB2WProduct): 
    type_key = 'http://odahub.io/ontology#ODABinaryProduct'
    
    def __init__(self, encoded_data, out_dir = None, name = 'bindata'):
        self.out_dir = out_dir
        self.name = name
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
    
    def __init__(self, text_data, out_dir = None, name = 'text'):
        self.out_dir = out_dir
        self.name = name
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
    
    def __init__(self, encoded_data, out_dir = None, name = 'picture'):
        self.name = name
        self.out_dir = out_dir
        self.data_prod = PictureProduct.decode(encoded_data)
        if self.data_prod.name:
            self.name = self.data_prod.name
        else:
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
    
    def __init__(self, encoded_data, out_dir = None, name = 'astropy_table'):
        self.name = name
        metadata = encoded_data.get('meta_data', {})
        self.out_dir = out_dir
        table_data_prod = ODAAstropyTable.decode(encoded_data)
        if table_data_prod.name:
            self.name = table_data_prod.name
        else:
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
        
    def __init__(self, encoded_data, out_dir = None, name = 'lc'):
        super().__init__(encoded_data, data_product_type = LightCurveProduct, out_dir = out_dir, name = name)
        
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
    
    def __init__(self, encoded_data, out_dir=None, name = 'spec'):
        super().__init__(encoded_data, data_product_type=SpectrumProduct, out_dir=out_dir, name = name)
        
class NB2WImageProduct(NB2WProduct):
    type_key = 'http://odahub.io/ontology#Image'
    
    def __init__(self, encoded_data, out_dir = None, name = 'image'):
        super().__init__(encoded_data, data_product_type = ImageProduct, out_dir = out_dir, name = name)
