import os
import re
import plistlib
import urllib.parse
from html.parser import HTMLParser

class HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.result = []
        self.ignore = False

    def handle_starttag(self, tag, attrs):
        if tag.lower() in ['script', 'style', 'head', 'title', 'meta']:
            self.ignore = True

    def handle_endtag(self, tag):
        if tag.lower() in ['script', 'style', 'head', 'title', 'meta']:
            self.ignore = False

    def handle_data(self, data):
        if not self.ignore:
            self.result.append(data)

    def get_text(self):
        return "".join(self.result).strip()

def extract_primary_url(text):
    """Extract clean target URL if present in payload (e.g. LinkedIn/Twitter/Safari shares)."""
    if not text:
        return None
    # 1. Google search redirect URL
    if 'google.com/url?' in text:
        match = re.search(r'https?://[^\s]*google\.com/url\?[^\s]+', text)
        if match:
            parsed = urllib.parse.urlparse(match.group(0))
            qs = urllib.parse.parse_qs(parsed.query)
            if 'q' in qs and qs['q']:
                return qs['q'][0]
            if 'url' in qs and qs['url']:
                return qs['url'][0]

    # 2. Extract standard HTTP/HTTPS URLs (excluding generic w3.org schemas)
    urls = re.findall(r'https?://[^\s<>"\'`,;\)]+', text)
    valid_urls = [u for u in urls if not u.startswith("http://www.w3.org") and not u.startswith("https://www.w3.org")]
    if valid_urls:
        return valid_urls[0]
    return None

def clean_html(html_str):
    if not html_str:
        return ""
    try:
        # Check if payload contains a shared link URL first
        target_url = extract_primary_url(html_str)
        if target_url and ("function " in html_str or "<html" in html_str.lower() or "<!doctype" in html_str.lower()):
            return target_url

        # Strip script, style, head, comments
        cleaned = re.sub(r'<script[^>]*>.*?</script>', '', html_str, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r'<style[^>]*>.*?</style>', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r'<head[^>]*>.*?</head>', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r'<!--.*?-->', '', cleaned, flags=re.DOTALL)
        
        parser = HTMLTextExtractor()
        parser.feed(cleaned)
        text = parser.get_text()
        if not text or len(text.strip()) == 0:
            text = re.sub(r'<[^<]+?>', '', cleaned).strip()
            
        # Clean up inline JS/CSS/sourceMapping junk lines
        lines = []
        for line in text.splitlines():
            l = line.strip()
            if not l:
                continue
            if any(k in l for k in [
                'sourceMappingURL=', 'window.google', 'closureDynamicButton',
                'Please click here if you are not redirected', 'SPDX-License-Identifier',
                'Copyright Google', 'getDfd()', 'window.lazyloader', 'window.tracking',
                'window.impressionTracking', 'window.ingraphTracking', 'window.appDetection',
                'window.pemTracking', 'window.appRedirectCompleted', 'User Agreement', 'Cookie Policy'
            ]):
                continue
            lines.append(l)
            
        final_text = "\n".join(lines).strip()
        
        if target_url:
            return target_url

        return final_text if final_text else html_str
    except Exception:
        clean = re.sub(r'<[^<]+?>', '', html_str).strip()
        return clean if clean else html_str

def extract_rtf_text(rtf_str):
    try:
        text = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: bytes.fromhex(m.group(1)).decode('latin1', errors='ignore'), rtf_str)
        text = re.sub(r'\{\\(?:fonttbl|colortbl|stylesheet|info|expandedcolortbl)[^{}]*\}', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\\[a-zA-Z0-9\-]+ ?', '', text)
        text = re.sub(r'[{}]', '', text)
        clean_lines = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith(';')]
        result = "\n".join(clean_lines).strip()
        result = re.sub(r'^[;\s]+', '', result)
        return result
    except Exception as e:
        print(f"[RTF PARSE ERROR] {e}", flush=True)
        return rtf_str

def extract_text_from_incoming(incoming_bytes):
    if not incoming_bytes:
        return ""
    
    # 1. Handle Apple Binary Plist (bplist00)
    idx = incoming_bytes.find(b'bplist00')
    if idx != -1:
        plist_bytes = incoming_bytes[idx:]
        try:
            plist = plistlib.loads(plist_bytes)
            if isinstance(plist, dict):
                if 'WebMainResource' in plist:
                    res_data = plist['WebMainResource'].get('WebResourceData', b'')
                    if res_data:
                        raw_str = res_data.decode('utf-8', errors='ignore')
                        return clean_html(raw_str)
                
                for key in ['public.utf8-plain-text', 'NSStringPboardType', 'NSString', 'WebResourceData']:
                    if key in plist:
                        val = plist[key]
                        if isinstance(val, bytes):
                            val = val.decode('utf-8', errors='ignore')
                        if isinstance(val, str) and val.strip():
                            if val.strip().startswith('{\\rtf1'):
                                return extract_rtf_text(val.strip())
                            return clean_html(val.strip())
                
                def find_strings_in_dict(d):
                    found = []
                    if isinstance(d, dict):
                        for k, v in d.items():
                            found.extend(find_strings_in_dict(v))
                    elif isinstance(d, list):
                        for item in d:
                            found.extend(find_strings_in_dict(item))
                    elif isinstance(d, bytes):
                        s = d.decode('utf-8', errors='ignore').strip()
                        if s and not s.startswith('http') and len(s) > 2:
                            if s.startswith('{\\rtf1'):
                                s = extract_rtf_text(s)
                            else:
                                s = clean_html(s)
                            found.append(s)
                    elif isinstance(d, str):
                        s = d.strip()
                        if s and not s.startswith('http') and len(s) > 2:
                            if s.startswith('{\\rtf1'):
                                s = extract_rtf_text(s)
                            else:
                                s = clean_html(s)
                            found.append(s)
                    return found
                
                strings = find_strings_in_dict(plist)
                meta_keys = {'WebMainResource', 'WebResourceMIMEType', 'WebResourceURL', 'WebResourceFrameName', 'WebResourceData', 'WebResourceTextEncodingName', 'text/html', 'UTF-8'}
                clean_strings = [s for s in strings if s not in meta_keys]
                if clean_strings:
                    return "\n".join(clean_strings)

            elif isinstance(plist, str):
                s = plist.strip()
                if s.startswith('{\\rtf1'):
                    return extract_rtf_text(s)
                return clean_html(s)
        except Exception as e:
            print(f"[PLIST PARSE ERROR] {e}", flush=True)

        return ""

    # 2. Standard Text / RTF / HTML
    raw_text = incoming_bytes.decode('utf-8', errors='ignore').strip()
    if raw_text.startswith('{\\rtf1'):
        return extract_rtf_text(raw_text)
    
    return clean_html(raw_text)
