"""Optional Inspect bridge. Importing this package never imports Inspect or heavy models.

The API composes the subprocess executor with the runner through callbacks;
layer-2 packages do not import one another. Keys and generation remain in the
parent, while Inspect owns task scheduling and its .eval transcript.
"""
