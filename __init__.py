# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

import time
import html
import requests
import traceback
import urllib
import itertools
import random
import re
import sys
import os

from bs4 import BeautifulSoup

from anki.template import TemplateRenderContext
from anki import hooks
from anki.hooks import addHook
from aqt.utils import tooltip, showInfo, showText
from aqt.qt import *
from aqt import mw

from . import lang
from . import form

addon_dir = os.path.dirname(os.path.realpath(__file__))
vendor_dir = os.path.join(addon_dir, 'vendor')
sys.path.append(vendor_dir)

headers = { "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/81.0.4044.122 Safari/537.36" }

def translate(query, options, is_filter=False):
    sourceLangCode = options['sourceLangCode']
    targetLangCode = options['targetLangCode']
    rmField = options.get('rmField', '')
    rmTargetField = options.get('rmTargetField', '')
    mdField = options.get('mdField', '')
    exField = options.get('exField', '')

    BASE_URL = "https://translate.googleapis.com/translate_a/single?client=gtx" \
        "&sl={}&tl={}&dt=t".format(sourceLangCode, targetLangCode)
    EXTRA_OPTIONS = "".join([
        "&dt=rm" if is_filter or rmField or rmTargetField else "",
        "&dt=md" if is_filter or mdField or exField else "",
        "&dt=ex" if is_filter or mdField or exField else "",
    ])
    GOOGLE_TRANSLATE_URL = BASE_URL + EXTRA_OPTIONS + "&q={}".format(query)

    try:
        r = requests.get(GOOGLE_TRANSLATE_URL, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        return parse_translated_data(data)
    except Exception:
        raise

def parse_translated_data(data):
    translated = ""
    romanization = ""
    romanizationTarget = ""
    for d in (data[0] or []):
        translated += d[0] if d is not None and len(d) > 0 and d[0] else ""
        romanization += d[3] if d is not None and len(d) > 3 and d[3] else ""
        romanizationTarget += d[2] if d is not None and len(d) > 2 and d[2] else ""
    definitions = []
    try:
        langcode = data[2]
    except IndexError:
        langcode = ""
    try:
        for d in data[12]:
            part_of_speech = d[0]
            definitions.append('<div class="eIKIse" style="color: #1a73e8; font-weight: bold;">{}</div>'.format(part_of_speech))
            definitions.append('<ol>')
            for m in d[1]:
                defn = m[0]
                try:
                    ex = m[2] or ""
                    if ex:
                        if langcode == 'ja':
                            ex = re.sub(r'^「(.+)」$', r' \1 ', ex)
                        defn += '<div class="MZgjEb" style="color: #5f6368; font-size: 19px;"'
                        if langcode:
                            defn += ' lang="{}"'.format(langcode)
                        defn += '><q>{}</q></div>'.format(ex)
                except IndexError:
                    pass
                definitions.append('<li class="fw3eif">{}</li>'.format(defn))
            definitions.append('</ol>')
    except IndexError:
        pass
    definitions = ''.join(definitions)
    examples = []
    try:
        for d in data[13][0]:
            ex = d[0]
            examples.append('<div class="AZPoqf">{}</div>'.format(ex))
    except IndexError:
        pass
    examples = ''.join(examples)
    return {
        "t": translated,
        "rm": romanization,
        "rmt": romanizationTarget,
        "md": definitions,
        "ex": examples
    }

sourceLanguages = None

def getSourceLanguages():
    global sourceLanguages
    if sourceLanguages is not None:
        return sourceLanguages
    sourceLanguages = {}
    for x in lang.source_languages:
        assert x["name"] not in sourceLanguages
        sourceLanguages[x["name"]] = x["code"]
    return sourceLanguages

targetLanguages = None

def getTargetLanguages():
    global targetLanguages
    if targetLanguages is not None:
        return targetLanguages
    targetLanguages = {}
    for x in lang.target_languages:
        assert x["name"] not in targetLanguages
        targetLanguages[x["name"]] = x["code"]
    return targetLanguages

class GoogleTranslate(QDialog):
    def __init__(self, context, nids=None) -> None:
        self.translator = None
        if nids is None:
            self.editor = context
            self.browser = None
            self.parentWindow = self.editor.parentWindow
            self.note = self.editor.note
            self.nids = [None]
        else:
            self.editor = None
            self.browser = context
            self.parentWindow = self.browser
            self.note = None
            self.nids = nids

        QDialog.__init__(self, self.parentWindow)

        self.form = form.Ui_Dialog()
        self.form.setupUi(self)

        self.sourceLanguages = getSourceLanguages()
        self.targetLanguages = getTargetLanguages()

        self.form.sourceLang.addItems(self.sourceLanguages)

        self.form.targetLang.addItems(self.targetLanguages)
        self.form.targetLang.setCurrentIndex(list(self.targetLanguages).index("English"))

        if not self.note:
            self.note = mw.col.getNote(nids[0])
        fields = [""] + self.note.keys()

        self.form.sourceField.addItems(fields)
        self.form.targetField.addItems(fields)
        self.form.rmTargetField.addItems(fields)
        self.form.rmField.addItems(fields)
        self.form.mdField.addItems(fields)
        self.form.exField.addItems(fields)
        self.form.atField.addItems(fields)

        def onSourceFieldChanged():
            self.sourceField = self.form.sourceField.currentText()
            if self.sourceField == "":
                return
            for fld, cb in [
                    ("Target Field", self.form.targetField),
                    ("Target Romanization Field", self.form.rmTargetField),
                    ("Romanization Field", self.form.rmField),
                    ("Definitions Field", self.form.mdField),
                    ("Examples Field", self.form.exField),
                    ("Alternative Translations Field", self.form.atField),
                ]:
                cb.clear()
                cb.addItems([f for f in fields if f != self.sourceField or (f == self.sourceField and fld == "Target Field")])
                if self.config[fld] in self.note:
                    idx = cb.findText(self.config[fld])
                    cb.setCurrentIndex(idx)

        self.config = mw.addonManager.getConfig(__name__)

        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.on_context_menu)

        self.form.sourceField.currentIndexChanged.connect(onSourceFieldChanged)

        if self.config["Source Field"] in self.note:
            self.form.sourceField.setCurrentIndex(fields.index(self.config["Source Field"]))
        else:
            self.form.sourceField.setCurrentIndex(1)

        for key, cb in [("Source Language", self.form.sourceLang), ("Target Language", self.form.targetLang)]:
            if self.config[key]:
                cb.setCurrentIndex(cb.findText(self.config[key]))

        if self.config["Strip HTML"]:
            self.form.radioButtonText.setChecked(True)
        else:
            self.form.radioButtonHTML.setChecked(True)

        self.form.checkBoxOverwrite.setChecked(self.config["Overwrite"])
        self.form.checkBoxTranslatedDefinitions.setChecked(self.config["Get Translated Definitions"])
        self.form.checkBoxTranslatedExamples.setChecked(self.config["Get Translated Examples"])
        if not self.config["Show Extra Options"]:
            self.form.extraOptions.setHidden(True)
        if not self.config["Show Extra Fields"]:
            self.form.extraFields.setHidden(True)

        self.icon = os.path.join(os.path.dirname(__file__), "favicon.png")
        self.setWindowIcon(QIcon(self.icon))

        self.adjustSize()
        self.show()

    def on_context_menu(self, point):
        m = QMenu()

        a = m.addAction("Show Extra Fields")
        a.triggered.connect(self.on_context_menu_show_extra_fields)
        a.setCheckable(True)
        a.setChecked(self.config["Show Extra Fields"])

        a = m.addAction("Show Extra Options")
        a.triggered.connect(self.on_context_menu_show_extra_options)
        a.setCheckable(True)
        a.setChecked(self.config["Show Extra Options"])

        sm = m.addMenu("Alternative Translations")
        a = sm.addAction("Show Meanings")
        a.setCheckable(True)
        a.triggered.connect(lambda: self.on_context_menu_alternative_translations_meanings("show"))
        if self.config["Alternative Translations Meanings Visibility"] == "show":
            a.setChecked(True)
        a = sm.addAction("Hide Meanings")
        a.setCheckable(True)
        a.triggered.connect(lambda: self.on_context_menu_alternative_translations_meanings("hide"))
        if self.config["Alternative Translations Meanings Visibility"] == "hide":
            a.setChecked(True)
        a = sm.addAction("Remove Meanings")
        a.setCheckable(True)
        a.triggered.connect(lambda: self.on_context_menu_alternative_translations_meanings("remove"))
        if self.config["Alternative Translations Meanings Visibility"] == "remove":
            a.setChecked(True)

        m.exec_(QCursor.pos())

    def on_context_menu_show_extra_options(self):
        self.config["Show Extra Options"] = not self.config["Show Extra Options"]
        mw.addonManager.writeConfig(__name__, self.config)
        self.form.extraOptions.setHidden(not self.config["Show Extra Options"])
        self.adjustSize()
        self.update()

    def on_context_menu_show_extra_fields(self):
        self.config["Show Extra Fields"] = not self.config["Show Extra Fields"]
        mw.addonManager.writeConfig(__name__, self.config)
        self.form.extraFields.setHidden(not self.config["Show Extra Fields"])
        self.adjustSize()
        self.update()

    def on_context_menu_alternative_translations_meanings(self, value):
        self.config["Alternative Translations Meanings Visibility"] = value
        mw.addonManager.writeConfig(__name__, self.config)

    def chunkify(self):
        chunk = {"nids": [], "query": "", "progress": 0}
        for nid in self.nids:
            if self.editor:
                note = self.note
            else:
                note = mw.col.getNote(nid)
            chunk["progress"] += 1
            if not note[self.sourceField]:
               continue
            if self.sourceField not in note:
                continue
            flag = False
            for fld in [self.targetField, self.rmTargetField, self.rmField, self.mdField, self.exField, self.atField]:
                if not fld:
                    continue
                if self.config["Overwrite"] or note[fld] == "":
                    flag = True
            if not flag:
                continue
            soup = BeautifulSoup(note[self.sourceField], "html.parser")
            text = soup.get_text()
            if len(text.split()) == 1 and (self.mdField or self.exField or self.atField):
                batch_translate = False
            else:
                batch_translate = True
            if not self.config["Strip HTML"] and batch_translate:
                text = note[self.sourceField]
            text = re.sub(r'{{c(\d+)::(.*?)(::.*?)?}}', r'<c\1>\2</c>', text, flags=re.I)
            text = urllib.parse.quote(text)
            if not chunk["nids"]:
                chunk["nids"].append(nid)
                chunk["query"] += text
                chunk["batch_translate"] = batch_translate
            elif chunk["batch_translate"] is False or batch_translate is False:
                yield chunk
                chunk = {"nids": [nid], "query": text, "progress": chunk["progress"], "batch_translate": batch_translate}
            elif len(chunk["query"] + text) < 2000:
                chunk["nids"].append(nid)
                chunk["query"] += urllib.parse.quote("\n~~~\n") + text
            else:
                yield chunk
                chunk = {"nids": [nid], "query": text, "progress": chunk["progress"], "batch_translate": batch_translate}
        if chunk["nids"]:
            yield chunk

    def fix(self, text):
        text = re.sub(r'<\s*/\s*', '</', text)
        soup = BeautifulSoup(text, "html.parser")
        for s in soup.select('[style]'):
            # rgb (34, 34, 34) -> rgb(34, 34, 34)
            s["style"] = re.sub(r' \(', '(', s["style"])
            s["style"] = re.sub(r'\s*([=:;])\s*', r'\1', s["style"])
            s["style"] = s["style"].strip()
        return str(soup)

    def sleep(self, seconds):
        start = time.time()
        while time.time() - start < seconds:
            time.sleep(0.01)
            QApplication.instance().processEvents()

    def accept(self):
        QDialog.accept(self)

        self.sourceField = self.form.sourceField.currentText()
        self.targetField = self.form.targetField.currentText()
        self.rmTargetField = self.form.rmTargetField.currentText()
        self.rmField = self.form.rmField.currentText()
        self.mdField = self.form.mdField.currentText()
        self.exField = self.form.exField.currentText()
        self.atField = self.form.atField.currentText()

        self.config["Source Field"] = self.sourceField
        self.config["Target Field"] = self.targetField
        self.config["Target Romanization Field"] = self.rmTargetField
        self.config["Romanization Field"] = self.rmField
        self.config["Definitions Field"] = self.mdField
        self.config["Examples Field"] = self.exField
        self.config["Alternative Translations Field"] = self.atField

        self.sourceLang = self.form.sourceLang.currentText()
        self.targetLang = self.form.targetLang.currentText()

        self.config["Source Language"] = self.sourceLang
        self.config["Target Language"] = self.targetLang

        self.config["Strip HTML"] = self.form.radioButtonText.isChecked()

        self.config["Overwrite"] = self.form.checkBoxOverwrite.isChecked()
        self.config["Get Translated Definitions"] = self.form.checkBoxTranslatedDefinitions.isChecked()
        self.config["Get Translated Examples"] = self.form.checkBoxTranslatedExamples.isChecked()

        mw.addonManager.writeConfig(__name__, self.config)

        self.sourceLangCode = self.sourceLanguages[self.sourceLang]
        self.targetLangCode = self.targetLanguages[self.targetLang]

        assert self.config["Alternative Translations Meanings Visibility"] in ["show", "hide", "remove"], self.config["Alternative Translations Meanings Visibility"]

        if self.sourceField == "":
            return

        if self.browser:
            self.browser.mw.progress.start(parent=self.browser)
            self.browser.mw.progress._win.setWindowIcon(QIcon(self.icon))
            self.browser.mw.progress._win.setWindowTitle("Google Translate")

        self.updated = False

        error = None
        try:
            for num, chunk in enumerate(self.chunkify(), 1):
                if self.browser:
                    if self.browser.mw.progress._win.wantCancel:
                        return
                    if num % 15 == 0:
                        self.browser.mw.progress.update("Sleeping for 30 seconds...")
                        self.browser.mw.autosave()
                        self.sleep(30)
                    elif num != 1:
                        timeout = random.randint(4,8)
                        self.sleep(5) if not (self.mdField or self.exField or self.atField) else self.sleep(timeout)

                nids = chunk["nids"]
                query = chunk["query"]

                attributes = {}
                idx = itertools.count(1)
                def attrs_to_i(m):
                    i = str(next(idx))
                    attributes[i] = m.group(2)
                    return "<{} i={}>".format(m.group(1), i)
                query = re.sub(r'<(\w+) ([^>]+)>', attrs_to_i, query)

                rows = query.split(urllib.parse.quote("\n~~~\n"))
                assert len(nids) == len(rows), "Chunks: {} != {}".format(len(nids), len(rows))

                options = {}
                options['sourceLangCode'] = self.sourceLangCode
                options['targetLangCode'] = self.targetLangCode
                if self.config["Show Extra Fields"]:
                    options['rmField'] = self.rmField
                    options['rmTargetField'] = self.rmTargetField
                    options['mdField'] = self.mdField
                    options['exField'] = self.exField

                result = translate(query, options)
                translated, romanization, romanizationTarget, definitions, examples = result.values()

                def i_to_attrs(m):
                    return "<{} {}>".format(m.group(1), attributes[m.group(2)])
                translated = re.sub(r'<(\w+) i\s*=\s*(\d+)>', i_to_attrs, translated)

                translated = re.split(r'\n[~〜] ?[~〜] ?[~〜]\n', translated)
                assert len(nids) == len(translated), "Translated: {} notes != {}\n\n-------------\n{}\n-------------\n".format(len(nids), len(translated), urllib.parse.unquote(query))

                romanization = re.split(r'\s*[~〜]{3}\s*', romanization)
                romanization += [""] * (len(nids) - len(romanization))
                assert len(nids) == len(romanization), "Romanization: {} notes != {}\n\n-------------\n{}\n-------------\n".format(len(nids), len(romanization), urllib.parse.unquote(query))

                romanizationTarget = re.split(r'\s*[~〜]{3}\s*', romanizationTarget)
                romanizationTarget += [""] * (len(nids) - len(romanizationTarget))
                assert len(nids) == len(romanizationTarget), "romanization target: {} notes != {}\n\n-------------\n{}\n-------------\n".format(len(nids), len(romanizationTarget), urllib.parse.unquote(query))

                if self.config["Show Extra Options"] and (self.config["Get Translated Definitions"] or self.config["Get Translated Examples"]) and len(nids) == 1 and (self.mdField or self.exField):
                    EXTRA_OPTIONS = "".join([
                        "&dt=rm" if self.rmField or self.rmTargetField else "",
                        "&dt=md" if self.mdField or self.exField else "",
                        "&dt=ex" if self.mdField or self.exField else "",
                    ])
                    BASE_URL = "https://translate.googleapis.com/translate_a/single?client=gtx" \
                    "&sl={}&tl={}&dt=t".format(self.targetLangCode, self.sourceLangCode)
                    GOOGLE_TRANSLATE_URL = BASE_URL + EXTRA_OPTIONS + "&q={}".format(translated[0])
                    r = requests.get(GOOGLE_TRANSLATE_URL, headers=headers, timeout=15)
                    r.raise_for_status()
                    data = r.json()
                    _, _, _, t_definitions, t_examples = parse_translated_data(data).values()
                    if self.config["Get Translated Definitions"]:
                        definitions = t_definitions
                    if self.config["Get Translated Examples"]:
                        examples = t_examples

                alt_translations = ''
                if self.config["Show Extra Fields"] and self.atField and len(nids) == 1:
                    if self.translator is None:
                        from googletrans import Translator
                        self.translator = Translator()
                    translation = self.translator.translate(urllib.parse.unquote(query), src=self.sourceLangCode, dest=self.targetLangCode)
                    data = translation.extra_data['parsed']
                    freq_color_blue = 'rgb(26,115,232)'
                    freq_color_gray = 'rgb(218,220,224)'
                    freq_info = '<span class="YF3enc" style="padding:7px 0px;display:inline-flex;"><div class="{}" style="border-radius:1px;height:6px;margin:1px;width:10px;background-color:{};"></div><div class="{}" style="border-radius:1px;height:6px;margin:1px;width:10px;background-color:{};"></div><div class="{}" style="border-radius:1px;height:6px;margin:1px;width:10px;background-color:{};"></div></span>'
                    try:
                        for d in data[3][5][0]:
                            part_of_speech = d[0]
                            tbody_padding_top = ''
                            if alt_translations:
                                tbody_padding_top = 'padding-top:1em;'
                            alt_translations += '<tbody>'
                            alt_translations += '<tr><th colspan="3" style="color:#1a73e8;font-weight:bold;text-align:left;{}">{}</th></tr>'.format(tbody_padding_top, part_of_speech)
                            for t in d[1]:
                                freq_colors = {
                                    1: ('EiZ8Dd', freq_color_blue, 'EiZ8Dd', freq_color_blue, 'EiZ8Dd', freq_color_blue),
                                    2: ('EiZ8Dd', freq_color_blue, 'EiZ8Dd', freq_color_blue, 'fXx9Lc', freq_color_gray),
                                    3: ('EiZ8Dd', freq_color_blue, 'fXx9Lc', freq_color_gray, 'fXx9Lc', freq_color_gray),
                                }[t[3]]
                                freq = freq_info.format(*freq_colors)
                                alt_translations += '<tr><td>{}</td>'.format(t[0])
                                if self.config["Alternative Translations Meanings Visibility"] == "remove":
                                    alt_translations += '<td></td>'
                                elif self.config["Alternative Translations Meanings Visibility"] == "hide":
                                    alt_translations += '<td style="font-size: 0;">{}</td>'.format(', '.join(t[2]))
                                else:
                                    alt_translations += '<td style="color: #5f6368; font-size: 19px;">{}</td>'.format(', '.join(t[2]))
                                alt_translations += '<td>{}</td></tr>'.format(freq)
                            alt_translations += '</tbody>'
                        alt_translations = '<table>' + alt_translations + '</table>'
                    except TypeError:
                        pass
                    except IndexError:
                        pass

                for nid, text, rom, romTarget in zip(nids, translated, romanization, romanizationTarget):
                    if not self.editor:
                        self.note = mw.col.getNote(nid)
                    text = re.sub(r' (<c\d+>) ', r' \1', text)
                    text = re.sub(r' (</c\d+>) ', r'\1 ', text)
                    text = re.sub(r'<c(\d+)>(.*?)</c>', r'{{c\1::\2}}', text)
                    text = re.sub(r' }}([,.?!])', r'}}\1', text)
                    text = re.sub(r'{{c(\d+)::(.*?) +}} ', r'{{c\1::\2}} ', text)
                    text = re.sub(r' ([,:;!?])', r'\1', text)
                    text = text.replace('< ', '<')
                    text = text.strip()
                    if not self.config["Strip HTML"]:
                        text = self.fix(text)

                    def saveField(fld, txt):
                        if not fld:
                            return
                        if self.note[fld] != txt:
                            self.updated = True
                        if self.config["Overwrite"] or self.note[fld] == "":
                            self.note[fld] = txt

                    saveField(self.targetField, text)
                    if self.config["Show Extra Fields"]:
                        saveField(self.rmTargetField, romTarget)
                        saveField(self.rmField, rom)
                        saveField(self.mdField, definitions)
                        saveField(self.exField, examples)
                        saveField(self.atField, alt_translations)

                    if self.editor:
                        self.editor.setNote(self.note)
                    else:
                        self.note.flush()

                if self.browser:
                    self.browser.mw.progress.update("Processed {}/{} notes...".format(chunk["progress"], len(self.nids)))
        except Exception as e:
            error = traceback.format_exc()
        finally:
            if self.browser:
                self.browser.mw.progress.finish()
                self.browser.mw.reset()

        if self.browser:
            mw.col.save()

        if error:
            showText('Error:\n\n' + str(error), parent=self.parentWindow)
        elif self.browser:
            showInfo("Processed {} notes.".format(len(self.nids)), parent=self.browser)
        elif self.editor and not self.updated:
            tooltip("No fields updated.", parent=self.parentWindow)


def onGoogleTranslate(browser):
    nids = browser.selectedNotes()

    if not nids:
        return tooltip("No cards selected.")

    GoogleTranslate(browser, nids)


def setupMenu(browser):
  a = QAction("Google Translate", browser)
  a.triggered.connect(lambda: onGoogleTranslate(browser))
  browser.form.menuEdit.addSeparator()
  browser.form.menuEdit.addAction(a)


addHook("browser.setupMenus", setupMenu)


def onSetupEditorButtons(buttons, editor):
    icon = os.path.join(os.path.dirname(__file__), "favicon.png")
    b = editor.addButton(icon,
                         "Google Translate",
                         lambda e=editor: GoogleTranslate(e),
                         tip="{}".format("Google Translate"))
    buttons.append(b)
    return buttons

from aqt.gui_hooks import editor_did_init_buttons
editor_did_init_buttons.append(onSetupEditorButtons)


lookup_cache = {}

# https://github.com/ankitects/anki-addons/blob/main/demos/field_filter/__init__.py
def my_field_filter(
    field_text: str,
    field_name: str,
    filter_name: str,
    context: TemplateRenderContext,
) -> str:
    if not filter_name.startswith("google-translate"):
        return field_text

    try:
        (_, name) = filter_name.split(' ', maxsplit=1)
    except ValueError:
        return invalid_name(filter_name)

    if name == 'voices':
        voices = '<div style="text-align: center; font-size: smaller;">'
        voices += '<table cellpadding="5" cellspacing="5" border="1" style="text-align: left; border-collapse: collapse; display: inline-block; margin: 20px;">'
        voices += f'<tr style="text-align: center;"><th colspan="2">Source Languages</th></tr>'
        for item in getSourceLanguages().items():
            voices += f'<tr><td>{item[1]}</td><td>{item[0]}</td></tr>'
        voices += '</table>'
        voices += '<table cellpadding="5" cellspacing="5" border="1" style="text-align: left; border-collapse: collapse; display: inline-block; margin: 20px;">'
        voices += f'<tr style="text-align: center;"><th colspan="2">Target Languages</th></tr>'
        for item in getTargetLanguages().items():
            voices += f'<tr><td>{item[1]}</td><td>{item[0]}</td></tr>'
        voices += '</table>'
        voices += '</div>'
        return voices

    if name == 'usage':
        usage = '<div style="font-size: smaller; color: #4B8CF5; text-align: left;">'
        usage += '<br>'.join([
            r"usage:",
            r"",
            r"-- get a list of language codes",
            r"{{google-translate voices:}}",
            r"",
            r"-- syntax",
            html.escape(r"{{google-translate <data_type> <source_language>,<target_language>[,<format>,<if_not_empty>] <source_field>:<target_field>}}"),
            html.escape(r"-- <data_type> = t, rm, rmt, md, ex"),
            html.escape(r"-- <format> = text"),
            html.escape(r"-- <if_not_empty> = s, skip, w, write (s = skip, w = write)"),
            r"",
            r"-- full syntax",
            r"{{google-translate t auto,en,text,s Example:Translation}}",
            r"{{google-translate t auto,en,text,w Example:Translation}}",
            r"",
            r"-- short syntax",
            r"{{google-translate t auto,en Example:Translation}}",
            r"-- auto,en = auto,en,text,s",
            r"",
            r"-- examples",
            r"{{google-translate t … Example:Translation}}",
            r"{{google-translate rm … Word:Romanization}}",
            r"{{google-translate rmt … Word:Target Romanization}}",
            r"{{google-translate md … Word:Definitions}}",
            r"{{google-translate ex … Word:Examples}}",
        ])
        usage += '</div>'
        return usage

    try:
        (mode, settings, source_field) = name.split(' ', maxsplit=2)
    except ValueError:
        return invalid_name(filter_name)

    try:
        assert mode in ["t", "rm", "rmt", "md", "ex"], mode
        settings = settings.split(',')
        if len(settings) == 2:
            (sl, tl), fmt, ie = settings, 'text', 's'
        elif len(settings) == 4:
            sl, tl, fmt, ie = settings
        else:
            return invalid_name(filter_name)
        assert sl in getSourceLanguages().values(), f"unknown language code '{sl}'"
        assert tl in getTargetLanguages().values(), f"unknown language code '{tl}'"
        assert fmt in ['text'], f"'{fmt}' not in ['text']"
        assert ie in ['s', 'skip', 'w', 'write'], f"'{ie}' not in ['s', 'skip', 'w', 'write']"
    except AssertionError as e:
        return invalid_name(filter_name, msg='error: <span style="color: black;">' + str(e) + '</span>')

    try:
        assert source_field in context.note()
    except AssertionError as e:
        return invalid_name(filter_name, msg=f"error: there is no field called '{source_field}'")

    if context._template is not None:
        return '{{' + filter_name + ':' + field_name + '}}'

    if field_name and context.note()[field_name] != "" and ie in ["s", "skip"]:
        return field_text

    options = {}
    options['sourceLangCode'] = sl
    options['targetLangCode'] = tl

    soup = BeautifulSoup(context.note()[source_field], "html.parser")
    query = soup.get_text()

    if not query:
        return field_text

    key = f"{sl} {tl} {query}"
    if key in lookup_cache:
        result = lookup_cache[key]
    else:
        result = translate(query, options, is_filter=True)
        lookup_cache[key] = result

    field_text = result[mode]

    if field_name:
        context.note()[field_name] = field_text
        context.note().flush()

    return field_text

def invalid_name(filter_name: str, msg="") -> str:
    err = '<br>'.join([
        f'invalid filter name: <span style="color: black;">{filter_name}</span>',
        msg,
        r"for some examples: {{google-translate usage:}}"
    ])
    return f'<div style="font-size: smaller; color: #f44336; text-align: left;">{err}</div>'

hooks.field_filter.append(my_field_filter)
