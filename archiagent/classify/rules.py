"""Offline layer hints; instance recognition still validates individual geometry."""
from __future__ import annotations

import re
from archiagent.classify.layers import LayerDecision
from archiagent.classify.roles import Role


def named_role(name: str) -> Role:
    words = set(re.findall(r"[a-z]+", name.lower()))
    groups = [(Role.WALL_STRUCTURAL,{"wall","walls","walll"}),
              (Role.COLUMN,{"column","columns","colum","col"}),
              (Role.BEAM_OVERHEAD,{"beam","beams"}),
              (Role.DOOR,{"door","doors","sliding","pocket"}),
              (Role.WINDOW,{"window","windows","win","glaz"}),
              (Role.STAIR,{"stair","stairs","sstair"}),
              (Role.FURNITURE,{"furniture","furn","fur","loose"}),
              (Role.ELECTRICAL,{"electrical","ele","bulb","fan","socket","switch","light","conduit"}),
              (Role.PLUMBING,{"plumbing","sanitary","sink","wc","basin","toilet","drain"}),
              (Role.GRID,{"grid","grids"}),
              (Role.DIMENSION,{"dim","dimension","dimensions"}),
              (Role.TEXT_LABEL,{"text","tex","label"}),
              (Role.TITLE_BLOCK,{"sheet","title","format"}),
              (Role.VEHICLE,{"car","cars","vehicle"})]
    return next((role for role,tokens in groups if words & tokens),Role.IGNORE)


class RuleClassifier:
    def classify(self,stats):
        return tuple(LayerDecision(s.name,named_role(s.name),
                                   .8 if named_role(s.name)!=Role.IGNORE else 0,
                                   "offline name hint; instance geometry remains inferred", "manual") for s in stats)
