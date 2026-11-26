"""Normalize generated OOXML numbering and collect unresolved references.

No pagination is guessed: Word refreshes real page fields when available; the
fallback TOC contains current heading text without stale page-number caches.
"""
from copy import deepcopy
from pathlib import Path
import re
from zipfile import ZipFile
from lxml import etree as E

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
NS = {'w': W}
def q(tag): return '{'+W+'}'+tag
def text(node): return ''.join(node.xpath('.//w:t/text()', namespaces=NS))
def paragraph(value=''):
    p = E.Element(q('p')); add_text(p,value); return p
def add_text(parent,value):
    r=E.SubElement(parent,q('r')); t=E.SubElement(r,q('t')); t.text=value
    t.set('{http://www.w3.org/XML/1998/namespace}space','preserve')
def set_text(p,value):
    for child in list(p):
        if child.tag not in (q('pPr'),q('bookmarkStart'),q('bookmarkEnd')): p.remove(child)
    add_text(p,value)
def no_number(p):
    pp=p.find(q('pPr'))
    if pp is None: pp=E.Element(q('pPr')); p.insert(0,pp)
    for old in pp.findall(q('numPr')): pp.remove(old)
    num=E.SubElement(pp,q('numPr')); E.SubElement(num,q('numId')).set(q('val'),'0')
def field(parent,instruction,cache=''):
    for kind in ('begin','separate','end'):
        r=E.SubElement(parent,q('r')); c=E.SubElement(r,q('fldChar')); c.set(q('fldCharType'),kind)
        if kind=='begin':
            c.set(q('dirty'),'true')
            r=E.SubElement(parent,q('r')); E.SubElement(r,q('instrText')).text=' '+instruction+' '
        if kind=='separate': add_text(parent,cache)

def _remove_toc(root):
    """Discard the entire complex TOC field, including nested PAGEREF caches."""
    body=root.find(q('body')); start=None; end=None; depth=0; active=False
    for p in body.xpath('.//w:p',namespaces=NS):
        for node in p.iter():
            if node.tag==q('fldChar'):
                kind=node.get(q('fldCharType'))
                if kind=='begin': depth+=1
                if kind=='end':
                    depth-=1
                    if active and depth==0: end=p; break
            elif node.tag==q('instrText') and re.match(r'\s*TOC\b',node.text or ''):
                start=p; active=True
        if end is not None: break
    if start is None: return None
    # Bundled templates use a body-level field or an SDT-wrapped field.
    a=start
    while a.getparent() is not body: a=a.getparent()
    b=end if end is not None else start
    while b.getparent() is not body: b=b.getparent()
    index=body.index(a)
    for node in list(body)[index:body.index(b)+1]: body.remove(node)
    return index

def _reference_fields(p,bookmarks,issues,part):
    """Resolve local REF fields; replace missing targets by explicit manual items."""
    children=list(p); stack=[]; spans=[]
    for i,child in enumerate(children):
        for node in child.iter():
            if node.tag==q('fldChar'):
                kind=node.get(q('fldCharType'))
                if kind=='begin': stack.append([i,''])
                elif kind=='end' and stack:
                    a,code=stack.pop()
                    if re.match(r'\s*(REF|PAGEREF)\b',code): spans.append((a,i,code))
            elif node.tag==q('instrText') and stack: stack[-1][1]+=node.text or ''
    for a,b,code in reversed(spans):
        match=re.match(r'\s*(REF|PAGEREF)\s+(\S+)',code)
        if not match: continue
        target=match.group(2).strip('"'); value=bookmarks.get(target)
        if value is None:
            value='【待补充交叉引用】'
            issues.append(('P0-01',part,code.strip(),'补充引用目标并人工核对：'+text(p)))
        elif match.group(1)=='PAGEREF':
            continue  # Valid page reference remains a real Word field.
        else:
            value=value or '【待补充交叉引用】'
        for child in children[a:b+1]:
            if child.getparent() is p: p.remove(child)
        replacement=paragraph(value)[0]; p.insert(min(a,len(p)),replacement)
    for simple in list(p.findall(q('fldSimple'))):
        code=simple.get(q('instr'),'')
        match=re.match(r'\s*(REF|PAGEREF)\s+(\S+)',code)
        if match and match.group(2) not in bookmarks:
            issues.append(('P0-01',part,code,'补充引用目标'))
            index=p.index(simple); p.remove(simple); p.insert(index,paragraph('【待补充交叉引用】')[0])


def highlight_manual_fields(p):
    """Highlight manual text in the existing pass without changing nearby text."""
    value = text(p)
    if '待人工填写' not in value:
        return
    spans = [m.span() for m in re.finditer(r'待人工填写(?:[：:][^\r\n]*)?', value)]
    offsets = {}
    offset = 0
    for node in p.xpath('.//w:t', namespaces=NS):
        offsets[node] = offset
        offset += len(node.text or '')
    for run in list(p.xpath('.//w:r', namespaces=NS)):
        if run.xpath('ancestor::w:p[1]', namespaces=NS)[0] is not p:
            continue
        if not any(a < offsets[node] + len(node.text or '') and b > offsets[node]
                   for node in run.findall(q('t')) for a, b in spans):
            continue
        parent = run.getparent()
        index = parent.index(run)
        # Split the touched run only; preserve all non-text run children.
        for child in list(run):
            if child.tag == q('rPr'):
                continue
            pieces = [(None, False)]
            if child.tag == q('t'):
                content, start = child.text or '', offsets[child]
                cuts = {0, len(content)}
                for a, b in spans:
                    if a < start + len(content) and b > start:
                        cuts.update((max(a-start, 0), min(b-start, len(content))))
                segments = sorted(cuts)
                pieces = [(content[a:b], any(x <= start+a < y for x, y in spans))
                          for a, b in zip(segments, segments[1:])]
            for chunk, marked in pieces:
                replacement = E.Element(q('r'), attrib=dict(run.attrib))
                properties = run.find(q('rPr'))
                if properties is not None:
                    replacement.append(deepcopy(properties))
                if marked:
                    properties = replacement.find(q('rPr'))
                    if properties is None:
                        properties = E.SubElement(replacement, q('rPr'))
                    for old in properties.findall(q('highlight')):
                        properties.remove(old)
                    E.SubElement(properties, q('highlight')).set(q('val'), 'yellow')
                copy = deepcopy(child)
                if chunk is not None:
                    copy.text = chunk
                    copy.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
                replacement.append(copy)
                parent.insert(index, replacement)
                index += 1
        parent.remove(run)

def _toc_has_page_boundary(node):
    """Recognize an existing boundary through empty spacer paragraphs."""
    while node is not None and node.tag == q('p'):
        page_break = node.find('./w:pPr/w:pageBreakBefore', NS)
        if (page_break is not None and page_break.get(q('val'), '1') not in ('0', 'false', 'off')
                or node.xpath('.//w:br[@w:type="page"]', namespaces=NS)):
            return True
        section = node.find('./w:pPr/w:sectPr', NS)
        if section is not None:
            kind = section.find(q('type'))
            return kind is None or kind.get(q('val')) in ('nextPage', 'oddPage', 'evenPage')
        if text(node).strip() or node.xpath('.//w:drawing|.//w:pict|.//w:object|.//w:fldChar|.//w:instrText', namespaces=NS):
            return False
        node = node.getnext()
    return False


def finalize_docx(path):
    issues=[]
    with ZipFile(path) as z: parts={i.filename:(i,z.read(i)) for i in z.infolist()}
    root=E.fromstring(parts['word/document.xml'][1]); body=root.find(q('body'))
    # PAGE uses the section format; preserve starts and non-dash formats.
    for number in root.xpath('//w:pgNumType[@w:fmt="numberInDash"]',namespaces=NS):
        number.set(q('fmt'),'decimal')
    toc_index=_remove_toc(root)
    styles=E.fromstring(parts['word/styles.xml'][1])
    levels={}
    for style in styles:
        names=style.xpath('./w:name/@w:val',namespaces=NS)
        if names and re.fullmatch(r'heading [1-4]',names[0],re.I):
            levels[style.get(q('styleId'))]=int(names[0][-1])
    counters=[0]*9; headings=[]; captions=[]; bookmarks={}
    # Caption styles differ between the original templates.
    caption_styles={s.get(q('styleId')) for s in styles
                    if s.xpath('./w:name/@w:val',namespaces=NS) in (['caption'],['题注'],['表题'],['表'],['图'])}
    figure_styles={s.get(q('styleId')) for s in styles
                   if s.xpath('./w:name/@w:val',namespaces=NS)==['图']}
    for p in body.xpath('./w:p',namespaces=NS):
        t=text(p).strip(); sid=p.xpath('./w:pPr/w:pStyle/@w:val',namespaces=NS)
        level=levels.get(sid[0]) if sid else None
        if level and t:
            counters[level-1]+=1; counters[level:]=[0]*(9-level)
            label='.'.join(str(n) for n in counters[:level])
            title=re.sub(r'^\d+(?:\.\d+)*\s+', '',t)
            set_text(p,label+' '+title); no_number(p)
            headings.append((p,level,label+' '+title))
        elif t and (re.match(r'^(表|图)\s*\d+\s*',t) or
                    sid and sid[0] in caption_styles or
                    any(re.search(r'\bSEQ\s+(表|图|Table|Figure)\b',c) for c in p.xpath('.//w:instrText/text()',namespaces=NS))):
            figure=(t.startswith('图') or sid and sid[0] in figure_styles or
                    any(re.search(r'\bSEQ\s+(图|Figure)\b',c,re.I)
                        for c in p.xpath('.//w:instrText/text()',namespaces=NS)))
            kind='图' if figure else '表'
            old=re.match(r'^[表图]\s*(\d+)',t)
            title=re.sub(r'^[表图]\s*\d+\s*','',t).strip()
            number=1+sum(c[1]==kind for c in captions)
            label=kind+str(number)
            captions.append((p,kind,label,old.group(1) if old else None,title))
            set_text(p,label+' '+title); no_number(p)
    for p in body.xpath('.//w:p',namespaces=NS):
        for mark in p.findall(q('bookmarkStart')):
            bookmarks.setdefault(mark.get(q('name')),text(p))
    for p,kind,label,old,title in captions:
        for mark in p.findall(q('bookmarkStart')): bookmarks[mark.get(q('name'))]=label
    # Plain references use the unique old caption or the named caption nearby.
    caption_nodes={c[0] for c in captions}
    for p in body.xpath('./w:p',namespaces=NS):
        if p in caption_nodes or p.xpath('.//w:instrText',namespaces=NS): continue
        t=text(p)
        def ref(m):
            kind,old=m.group(1),m.group(2)
            options=[c for c in captions if c[1]==kind]
            named=[c for c in options if len(c[4])>=4 and (c[4] in t or re.sub(r'表$', '', c[4]) in t)]
            if named:
                longest = max(len(c[4]) for c in named)
                named = [c for c in named if len(c[4]) == longest]
            matches=named or [c for c in options if c[3]==old]
            if len(matches)==1: return matches[0][2]
            # A directly introduced following table is unambiguous.
            nxt=p.getnext()
            while nxt is not None and nxt.tag==q('p') and not text(nxt).strip(): nxt=nxt.getnext()
            for c in options:
                if c[0] is nxt: return c[2]
            issues.append(('P1-07','正文',m.group(),'人工核对表图引用：'+t))
            return '【待补充'+kind+'引用】'
        revised=re.sub(r'([表图])\s*(\d+)',ref,t)
        if revised!=t: set_text(p,revised)
    for name,(info,data) in list(parts.items()):
        if name!='word/document.xml' and not(name.startswith(('word/header','word/footer')) and name.endswith('.xml')): continue
        tree=root if name=='word/document.xml' else E.fromstring(data)
        for p in tree.xpath('//w:p',namespaces=NS):
            _reference_fields(p,bookmarks,issues,name)
            if re.search(r'Error[!:]?\s*Reference source not found|错误[!！].*引用源',text(p),re.I):
                issues.append(('P0-01',name,text(p),'人工补充交叉引用'))
                set_text(p,re.sub(r'Error[!:]?\s*Reference source not found\.?|错误[!！].*?引用源[。.]?', '【待补充交叉引用】',text(p),flags=re.I))
            if name.startswith('word/footer') and (re.search(r'共\s*\d+\s*页',text(p)) or p.xpath('.//w:instrText[contains(text(),"PAGE")]|.//w:fldSimple[contains(@w:instr,"PAGE")]',namespaces=NS)):
                set_text(p,'共 '); field(p,'NUMPAGES'); add_text(p,' 页 第 '); field(p,'PAGE'); add_text(p,' 页')
                props=p.find(q('pPr'))
                if props is None: props=E.Element(q('pPr')); p.insert(0,props)
                align=props.find(q('jc'))
                if align is None: align=E.SubElement(props,q('jc'))
                align.set(q('val'),'center')
            highlight_manual_fields(p)
        parts[name]=(info,E.tostring(tree,xml_declaration=True,encoding='UTF-8',standalone=True))
    if toc_index is not None:
        # Static linked outline avoids publishing fabricated pagination caches.
        toc=E.Element(q('sdt')); prop=E.SubElement(toc,q('sdtPr'))
        E.SubElement(prop,q('tag')).set(q('val'),'PRISM_CURRENT_OUTLINE')
        content=E.SubElement(toc,q('sdtContent'))
        add_text(E.SubElement(content,q('p')), '静态链接目录（页码未重算）')
        ids=[int(x) for x in root.xpath('//w:bookmarkStart/@w:id',namespaces=NS) if x.isdigit()]
        next_id=max(ids+[0])+1
        for p,level,title in headings:
            if level>2: continue
            anchor='PRISM_Heading_'+str(next_id)
            mark=E.Element(q('bookmarkStart')); mark.set(q('id'),str(next_id)); mark.set(q('name'),anchor); p.insert(1,mark)
            end=E.SubElement(p,q('bookmarkEnd')); end.set(q('id'),str(next_id)); next_id+=1
            entry=E.SubElement(content,q('p')); pp=E.SubElement(entry,q('pPr'))
            E.SubElement(pp,q('ind')).set(q('left'),str((level-1)*360))
            link=E.SubElement(entry,q('hyperlink')); link.set(q('anchor'),anchor); add_text(link,title)
        body.insert(min(toc_index,len(body)),toc)
        # The rebuilt directory has its own page, independent of dynamic length.
        next_node=toc.getnext()
        if next_node is not None and not _toc_has_page_boundary(next_node):
            if next_node.tag == q('p'):
                properties = next_node.find(q('pPr'))
                if properties is None:
                    properties = E.Element(q('pPr'))
                    next_node.insert(0, properties)
                boundary = properties.find(q('pageBreakBefore'))
                if boundary is None:
                    boundary = E.SubElement(properties, q('pageBreakBefore'))
                boundary.set(q('val'), '1')
            else:
                boundary=E.Element(q('p')); run=E.SubElement(boundary,q('r'))
                E.SubElement(run,q('br')).set(q('type'),'page'); toc.addnext(boundary)
    parts['word/document.xml']=(parts['word/document.xml'][0],E.tostring(root,xml_declaration=True,encoding='UTF-8',standalone=True))
    settings=E.fromstring(parts['word/settings.xml'][1]); update=settings.find(q('updateFields'))
    if update is None: update=E.SubElement(settings,q('updateFields'))
    update.set(q('val'),'true')
    parts['word/settings.xml']=(parts['word/settings.xml'][0],E.tostring(settings,xml_declaration=True,encoding='UTF-8',standalone=True))
    temporary=Path(path).with_suffix('.tmp')
    with ZipFile(temporary,'w') as z:
        for info,data in parts.values(): z.writestr(info,data)
    # Windows indexers/antivirus may briefly retain a newly written Office file.
    import time
    for attempt in range(6):
        try:
            temporary.replace(path)
            break
        except PermissionError:
            if attempt == 5: raise
            time.sleep(0.1 * (attempt + 1))
    return issues

def _save_word_page_count(path, page_count):
    """Keep NUMPAGES caches consistent with Word's completed pagination.

    Hidden Word can save partial-pagination results in first/even/section
    footers even after Fields.Update. Only the result text is synchronized;
    the live NUMPAGES fields and Word's section-specific PAGE fields remain.
    """
    def update_cache(code, nodes):
        if re.match(r'\s*NUMPAGES\b', code, re.I) and nodes:
            nodes[0].text = str(page_count)
            for node in nodes[1:]: node.text = ''

    with ZipFile(path) as archive:
        parts = [(info, archive.read(info)) for info in archive.infolist()]
    updated = []
    for info, data in parts:
        if info.filename.startswith('word/footer') and info.filename.endswith('.xml'):
            root = E.fromstring(data)
            for simple in root.xpath('//w:fldSimple', namespaces=NS):
                update_cache(simple.get(q('instr'), ''), simple.xpath('.//w:t', namespaces=NS))
            stack = []
            for node in root.iter():
                if node.tag == q('fldChar'):
                    kind = node.get(q('fldCharType'))
                    if kind == 'begin': stack.append({'code': '', 'result': False, 'nodes': []})
                    elif stack and kind == 'separate': stack[-1]['result'] = True
                    elif stack and kind == 'end':
                        field_data = stack.pop()
                        update_cache(field_data['code'], field_data['nodes'])
                elif stack and node.tag == q('instrText'):
                    stack[-1]['code'] += node.text or ''
                elif stack and stack[-1]['result'] and node.tag == q('t'):
                    stack[-1]['nodes'].append(node)
            data = E.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)
        updated.append((info, data))
    temporary = Path(path).with_suffix('.pages.tmp')
    with ZipFile(temporary, 'w') as archive:
        for info, data in updated: archive.writestr(info, data)
    import time
    for attempt in range(6):
        try:
            temporary.replace(path)
            break
        except PermissionError:
            if attempt == 5: raise
            time.sleep(0.1 * (attempt + 1))


def _record_word_ownership(word, ownership_callback):
    """Record the private Word PID while its identifying window still exists."""
    try:
        hwnd = word.Hwnd
    except AttributeError:
        probe = word.Documents.Add()
        try:
            ownership_callback(probe.ActiveWindow.Hwnd)
        finally:
            probe.Close(False)
    else:
        ownership_callback(hwnd)


def _update_word_fields(path, ownership_callback=None):
    """Use a private Word instance; callers must report unavailable pagination."""
    import pythoncom
    from win32com.client import DispatchEx
    with ZipFile(path) as archive:
        tree = E.fromstring(archive.read('word/document.xml'))
    expected_toc = len(tree.xpath('//w:sdt[w:sdtPr/w:tag[@w:val="PRISM_REAL_TOC"]]', namespaces=NS))
    pythoncom.CoInitialize(); word=None; document=None
    try:
        try:
            word=DispatchEx('Word.Application')
        except Exception as error:
            raise WordUnavailable(str(error)) from error
        word.Visible=False; word.DisplayAlerts=0
        if ownership_callback is not None:
            _record_word_ownership(word, ownership_callback)
        word.AutomationSecurity=3
        word.Options.UpdateLinksAtOpen=False
        document=word.Documents.Open(str(Path(path).resolve()),ReadOnly=False,AddToRecentFiles=False)
        previous = None
        for attempt in range(4):
            document.Repaginate()
            for story_index in range(1, 18):
                try:
                    current = document.StoryRanges(story_index)
                except Exception:
                    continue
                while current is not None:
                    current.Fields.Update()
                    current = current.NextStoryRange
            for index in range(1, document.TablesOfContents.Count + 1):
                document.TablesOfContents(index).Update()
                document.TablesOfContents(index).TabLeader = 1
            document.Repaginate()
            for index in range(1, document.TablesOfContents.Count + 1):
                document.TablesOfContents(index).UpdatePageNumbers()
            if document.TablesOfContents.Count < expected_toc:
                raise PaginationUnstable("Word did not create the requested TOC")
            page_count = document.ComputeStatistics(2)
            snapshot = _pagination_snapshot(document, page_count)
            if snapshot == previous and snapshot[1] != "pending":
                break
            previous = snapshot
        else:
            raise PaginationUnstable("Word pagination/TOC did not converge in four passes")
        document.Save()
    finally:
        if document is not None:
            try: document.Close(False)
            except Exception: pass
        if word is not None:
            try: word.Quit()
            except Exception: pass
        document=None; word=None
        pythoncom.CoUninitialize()
    _save_word_page_count(path, page_count)


class PaginationUnstable(RuntimeError):
    """Word could not establish a consistent final pagination."""


class WordUnavailable(RuntimeError):
    """A private Word automation application could not be created."""


def _pagination_snapshot(document, page_count):
    """Check every TOC page reference against its live destination."""
    if page_count < 1:
        raise PaginationUnstable("Word returned an invalid page count")
    entries = []
    for index in range(1, document.TablesOfContents.Count + 1):
        toc = document.TablesOfContents(index)
        references = 0
        for number in range(1, toc.Range.Fields.Count + 1):
            item = toc.Range.Fields(number)
            match = re.match(r'\s*PAGEREF\s+(\S+)', item.Code.Text)
            if match:
                target = match.group(1).strip('"')
                if not document.Bookmarks.Exists(target):
                    raise PaginationUnstable("TOC destination is missing: " + target)
                expected = document.Bookmarks(target).Range.Information(1)
                numbers = re.findall(r'\d+', item.Result.Text)
                if not numbers or int(numbers[-1]) != expected:
                    return (page_count, "pending", index, number, item.Result.Text, expected)
                references += 1
                entries.append((document.Bookmarks(target).Range.Text, expected))
        if not references:
            raise PaginationUnstable("Word TOC has no verifiable page references")
        entries.append(toc.Range.Text)
    return (page_count, tuple(entries))


def _prepare_real_toc(path):
    """Replace only PRISM's fresh static outline in the private Word copy."""
    with ZipFile(path) as archive:
        parts = [(info, archive.read(info)) for info in archive.infolist()]
    updated = []
    for info, data in parts:
        if info.filename == 'word/document.xml':
            root = E.fromstring(data)
            for toc in root.xpath('//w:sdt[w:sdtPr/w:tag[@w:val="PRISM_CURRENT_OUTLINE"]]', namespaces=NS):
                toc.find('./' + q('sdtPr') + '/' + q('tag')).set(q('val'), 'PRISM_REAL_TOC')
                content = toc.find(q('sdtContent'))
                for child in list(content):
                    content.remove(child)
                field(E.SubElement(content, q('p')), r'TOC \o "1-2" \h \z')
            data = E.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)
        updated.append((info, data))
    with ZipFile(path, 'w') as archive:
        for info, data in updated:
            archive.writestr(info, data)


def update_word_fields(path, ownership_callback=None):
    """Adopt a validated private copy; retain the intact static file on failure."""
    import os
    import shutil
    import tempfile
    path = Path(path).resolve()
    with tempfile.TemporaryDirectory(prefix='.prism-pagination-', dir=str(path.parent)) as folder:
        private = Path(folder) / 'document.docx'
        shutil.copy2(str(path), str(private))
        _prepare_real_toc(private)
        _update_word_fields(private, ownership_callback)
        os.replace(str(private), str(path))
