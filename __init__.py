# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

import time
import requests
import traceback
import urllib
import re

from bs4 import BeautifulSoup

from anki.hooks import addHook
from aqt.utils import tooltip, showInfo, showText
from aqt.qt import *
from aqt import mw

from . import lang
from . import form


class GoogleTranslate(QDialog):
    def __init__(self, browser, nids) -> None:
        QDialog.__init__(self, browser)
        self.browser = browser
        self.nids = nids
        self.form = form.Ui_Dialog()
        self.form.setupUi(self)

        self.sourceLanguages = {}
        for x in lang.source_languages:
            assert x["name"] not in self.sourceLanguages
            self.sourceLanguages[x["name"]] = x["code"]

        self.targetLanguages = {}
        for x in lang.target_languages:
            assert x["name"] not in self.targetLanguages
            self.targetLanguages[x["name"]] = x["code"]

        self.form.sourceLang.addItems(self.sourceLanguages)
        
        self.form.targetLang.addItems(self.targetLanguages)
        self.form.targetLang.setCurrentIndex(list(self.targetLanguages).index("English"))

        note = mw.col.getNote(nids[0])
        fields = note.keys()
        
        self.form.sourceField.addItems(fields)
        self.form.sourceField.setCurrentIndex(0)
        
        self.form.targetField.addItems(fields)
        self.form.targetField.setCurrentIndex(len(fields)-1)
        
        self.config = mw.addonManager.getConfig(__name__)
        
        for fld, cb in [("Source Field", self.form.sourceField), ("Target Field", self.form.targetField)]:
            if self.config[fld] and self.config[fld] in note:
                cb.setCurrentIndex(fields.index(self.config[fld]))

        for key, cb in [("Source Language", self.form.sourceLang), ("Target Language", self.form.targetLang)]:
            if self.config[key]:
                cb.setCurrentIndex(cb.findText(self.config[key]))

        if self.config["Strip HTML"]:
            self.form.radioButtonText.setChecked(True)
        else:
            self.form.radioButtonHTML.setChecked(True)

        self.form.checkBoxOverwrite.setChecked(self.config["Overwrite"])

        self.icon = os.path.join(os.path.dirname(__file__), "favicon.ico")
        self.setWindowIcon(QIcon(self.icon))

        self.show()

    def chunkify(self):
        chunk = {"nids": [], "query": "", "progress": 0}
        for nid in self.nids:
            note = mw.col.getNote(nid)
            chunk["progress"] += 1
            if not note[self.sourceField]:
               continue
            if self.sourceField not in note:
                continue
            if self.targetField not in note:
                continue
            if note[self.targetField] and not self.config["Overwrite"]:
                continue
            if self.config["Strip HTML"]:
                soup = BeautifulSoup(note[self.sourceField], "html.parser")
                text = soup.get_text()
            else:
                text = note[self.sourceField]
            text = re.sub(r'{{c(\d+)::(.*?)(::.*?)?}}', r'<c\1>\2</c>', text, flags=re.I)
            if not chunk["nids"]:
                chunk["nids"].append(nid)
                chunk["query"] += text
            elif len(chunk["query"] + text) < 2000:
                chunk["nids"].append(nid)
                chunk["query"] += "\n~\n" + text
            else:
                yield chunk
                chunk = {"nids": [nid], "query": text, "progress": chunk["progress"]}
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

        self.config["Source Field"] = self.sourceField
        self.config["Target Field"] = self.targetField

        self.sourceLang = self.form.sourceLang.currentText()
        self.targetLang = self.form.targetLang.currentText()

        self.config["Source Language"] = self.sourceLang
        self.config["Target Language"] = self.targetLang

        self.config["Strip HTML"] = self.form.radioButtonText.isChecked()

        self.config["Overwrite"] = self.form.checkBoxOverwrite.isChecked()

        mw.addonManager.writeConfig(__name__, self.config)

        self.sourceLangCode = self.sourceLanguages[self.sourceLang]
        self.targetLangCode = self.targetLanguages[self.targetLang]

        self.browser.mw.progress.start(parent=self.browser)

        self.browser.mw.progress._win.setWindowIcon(QIcon(self.icon))
        self.browser.mw.progress._win.setWindowTitle("Google Translate")
    
        error = None
        try: 
            for num, chunk in enumerate(self.chunkify(), 1):
                if num % 15 == 0:
                    self.browser.mw.progress.update("Sleeping for 30 seconds...")
                    self.sleep(30)
                elif num != 1:
                    self.sleep(5)

                nids = chunk["nids"]
                query = chunk["query"]
                
                rows = query.split("\n~\n")
                assert len(nids) == len(rows), "Chunks: {} != {}".format(len(nids), len(rows))

                GOOGLE_TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single?" \
                    "client=gtx&sl={}&tl={}&dt=t&q={}".format(self.sourceLangCode, self.targetLangCode, urllib.parse.quote(query))
                headers = { "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/81.0.4044.122 Safari/537.36" }
                
                try:
                    r = requests.get(GOOGLE_TRANSLATE_URL, headers=headers, timeout=15)
                    r.raise_for_status()
                    data = r.json()
                    translated = ""
                    for d in data[0]:
                        translated += d[0]
                except Exception:
                    raise

                translated = translated.split('\n~\n')
                assert len(nids) == len(translated), "Translated: {} != {}".format(len(nids), len(translated))

                for nid, text in zip(nids, translated):
                    note = mw.col.getNote(nid)
                    text = re.sub(r' (<c\d+>) ', r' \1', text)
                    text = re.sub(r' (</c\d+>) ', r'\1 ', text)
                    text = re.sub(r'<c(\d+)>(.*?)</c>', r'{{c\1::\2}}', text)
                    text = re.sub(r' }}([,.?!])', r'}}\1', text)
                    text = re.sub(r'{{c(\d+)::(.*?) +}} ', r'{{c\1::\2}} ', text)
                    text = text.replace('< ', '<')
                    text = text.strip()
                    if not self.config["Strip HTML"]:
                        text = self.fix(text)
                    note[self.targetField] = text
                    note.flush()

                self.browser.mw.progress.update("Processed {}/{} notes...".format(chunk["progress"], len(self.nids)))
        except Exception as e:
            error = traceback.format_exc()

        self.browser.mw.progress.finish()
        
        self.browser.mw.reset()
        
        mw.col.save()

        if error:
            showText('Error:\n\n' + str(error), parent=self.browser)
        else:
            showInfo("Processed {} notes.".format(len(self.nids)), parent=self.browser)

        
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
