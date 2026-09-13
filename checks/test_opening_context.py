"""OCR sill notes introduce uncertainty without inventing window semantics."""
from dataclasses import replace
import json
import math
import unittest

from archiagent.evidence import SourceEntity
from archiagent.primitives import PrimitiveSet, TextItem
from archiagent.recognition import contextualize_openings, host_openings
from checks.test_opening_localization import door, wall


def source(notes,units_per_foot=1):
    texts=[];entities=[]
    for i,(text,point,confidence,kind) in enumerate(notes):
        sid=f'ocr-{i}'
        x,y=(v*units_per_foot for v in point)
        texts.append(TextItem(text,(x-.01,y-.01,x+.01,y+.01),'OCR',sid))
        entities.append(SourceEntity(sid,kind,'OCR',metadata=(('confidence',str(confidence)),)))
    return PrimitiveSet((),tuple(texts),20,20,'context.dxf','context',entities=tuple(entities))


class OpeningContextChecks(unittest.TestCase):
    def test_confident_sill_note_marks_inferred_door_and_blocks_hosting(self):
        symbol=door((0,0),(3,0))
        ps=source([('SILL = 3 ft',(1.5,1),.95,'OCR_TEXT')])
        result=contextualize_openings(ps,(symbol,),1)[0]
        props=dict(result.properties)
        self.assertEqual(props['opening_type_ambiguous'],'true')
        evidence=json.loads(props['opening_context_evidence'])
        self.assertEqual(evidence[0]['source_id'],'ocr-0')
        self.assertEqual(evidence[0]['text'],'SILL = 3 ft')
        self.assertAlmostEqual(evidence[0]['distance_ft'],1)
        self.assertEqual(result.kind,'door')
        self.assertEqual(result.source_ids,symbol.source_ids)
        self.assertEqual(result.boundary,symbol.boundary)
        walls=(wall((-4,0),(0,0),'left'),wall((3,0),(7,0),'right'))
        actual,openings=host_openings(walls,(result,),10)
        self.assertEqual(actual,walls)
        self.assertEqual(openings,())
        self.assertEqual(contextualize_openings(ps,(result,),1),(result,))

    def test_low_confidence_native_text_and_distant_notes_are_ignored(self):
        symbol=door((0,0),(3,0))
        ps=source([('SILL',(1,1),.84,'OCR_TEXT'),('SILL',(1,1),1,'TEXT'),
                   ('SILL',(3.26,0),.99,'OCR_TEXT'),('SILL',(1,2.01),.99,'OCR_TEXT'),
                   ('WINDOW',(1,1),.99,'OCR_TEXT'),('SILL',(1,1),float('nan'),'OCR_TEXT')])
        self.assertEqual(contextualize_openings(ps,(symbol,),1),(symbol,))

    def test_reviewed_door_and_window_are_not_retyped_or_ambiguated(self):
        ps=source([('WINDOWSILL',(1.5,1),.9,'OCR_TEXT')])
        for evidence in ('reviewed-template','reviewed','manual','reviewed-instance','reviewed-symbol'):
            symbol=replace(door((0,0),(3,0)),evidence=evidence)
            actual=contextualize_openings(ps,(symbol,),1)[0]
            self.assertNotIn('opening_type_ambiguous',dict(actual.properties))
            self.assertEqual(actual.kind,'door')
            self.assertEqual(actual.height_ft,symbol.height_ft)
        window=replace(door((0,0),(3,0)),kind='window',evidence='block-metadata')
        actual=contextualize_openings(ps,(window,),1)[0]
        self.assertNotIn('opening_type_ambiguous',dict(actual.properties))
        self.assertEqual(actual.kind,'window')

    def test_normalized_text_rotated_axis_and_source_units(self):
        # The label projects exactly .25ft beyond the vertical span, and is
        # within 2ft perpendicular distance in model coordinates.
        symbol=door((10,10),(10,13))
        ps=source([('ｓｉｌｌ',(11.5,13.25),.85,'OCR_TEXT')],units_per_foot=12)
        actual=contextualize_openings(ps,(symbol,),12)[0]
        props=dict(actual.properties)
        self.assertEqual(props['opening_type_ambiguous'],'true')
        evidence=json.loads(props['opening_context_evidence'])[0]
        self.assertAlmostEqual(evidence['perpendicular_distance_ft'],1.5)
        self.assertAlmostEqual(evidence['along_axis_ft'],3.25)
        self.assertAlmostEqual(evidence['distance_ft'],math.hypot(1.5,.25))

    def test_explicit_type_review_marker_and_absent_axis(self):
        ps=source([('SILL',(1.5,1),.99,'OCR_TEXT')])
        symbol=door((0,0),(3,0))
        reviewed=replace(symbol,properties=symbol.properties+(('opening_type_reviewed','true'),))
        actual=contextualize_openings(ps,(reviewed,),1)[0]
        self.assertNotIn('opening_type_ambiguous',dict(actual.properties))
        unknown=replace(symbol,evidence='inferred',properties=())
        self.assertEqual(contextualize_openings(ps,(unknown,),1),(unknown,))
        for invalid in (0,-1,True,float('inf')):
            with self.assertRaises(ValueError):contextualize_openings(ps,(symbol,),invalid)


if __name__=='__main__':unittest.main()
