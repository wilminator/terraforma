"""Force disuse: the second degree of off for an add-on's components (see README, "Add-ons").

When an admin commits a component as ``disused`` (``gamestate.components``), the heroes that hold it have to stop: a hero whose job is
disused, a hero that knows a disused ability or carries a disused item. Nothing is removed: the item or ability is set aside with the
hero (``models.SetAside``) and comes back when the component is no longer disused. The change is not made at the commit but when the
player next signs in and asks about their hero (``routes``): a scene runs as a dialog (``service``), the hero can't fight until it is
done (``gate``), and a player who never returns leaves the hero as it was, in limbo, with everything else about it working.
"""
