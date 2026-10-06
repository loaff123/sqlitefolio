import unittest
from sqlitefolio.report import render

class ReportTests(unittest.TestCase):
    def test_static_escape_and_limitations(self):
        p={'format':'sqlitefolio.packet.v1','input':{'scenario':'demo','profile':{'foreign_keys':True}},'source':{'sha256':'a','snapshot_sha256':'b','kind':'sql','included':False},'input_sha256':'c','summary':[{'name':'test','status':'failed','execution':'completed','complete':True,'observed_checks':[{'name':'<script>bad</script>','status':'fail'}],'reopened_checks':[],'observed_changes':{},'reopened_changes':{}}],'candidates':[{'name':'test','runtime':{},'before':None,'observed':None,'reopened':None,'queries':{},'execution':{'error':'<img src=x onerror=alert(1)>'}}],'limitations':['Sample only'],'files':{}}
        html=render(p)
        self.assertNotIn('<script',html.lower());self.assertNotIn('<img ',html.lower())
        self.assertIn('&lt;script&gt;',html);self.assertIn('Observed in-session',html)
        self.assertIn('Reopened after controlled close',html)
        self.assertIn('Structural verification',html)
        self.assertNotIn('https://',html);self.assertNotIn('<link',html)
