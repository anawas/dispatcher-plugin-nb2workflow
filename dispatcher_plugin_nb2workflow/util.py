import logging
from html.parser import HTMLParser
from functools import wraps
from json import dumps

logger = logging.getLogger()

class AstropyTableViewParser(HTMLParser):
    def handle_starttag(self, tag, attrs):
        if tag == 'body':
            self.inbody = True
        if tag == 'script':
            self.inscript = True
        if getattr(self, 'intable', False):
            self.tabcode += self.get_starttag_text()
        if tag == 'table':
            self.intable = True
            attdic = dict(attrs)
            self.tabcode = f'<table class="{attdic["class"]} mmoda" id="{attdic["id"]}">'
 
            
    def handle_endtag(self, tag):
        if tag == 'body':
            self.inbody = False
        if tag == 'script':
            self.inscript = False
        if tag == 'table':
            self.intable = False
        if getattr(self, 'intable', False):
            self.tabcode += f"</{tag}>"
    
    def handle_data(self, data):
        if getattr(self, 'inbody', False) and getattr(self, 'inscript', False):
            self.script = data
        if getattr(self, 'intable', False):
            self.tabcode += data

class HashableDict(dict):
    def __hash__(self):  #  type: ignore
        return hash(dumps(self, sort_keys=True))

def with_hashable_dict(func):
    @wraps(func)
    def wrapper(*args, bk_descript_dict = {}, ontology_path = None):
        return func(*args, 
                    bk_descript_dict=HashableDict(bk_descript_dict), 
                    ontology_path=ontology_path)
    return wrapper
