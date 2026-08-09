# Marks `app/core/` as a package.
#
# "Core" = cross-cutting concerns that aren't tied to one feature. The test for
# whether something belongs here is whether *most* features would import it:
#
#   config.py    settings, read by everything
#   security.py  password hashing + JWT encode/decode (no FastAPI imports)
#   deps.py      shared FastAPI dependencies, incl. get_current_user (no crypto)
#
# security.py and deps.py are two files rather than one on purpose — see the
# module docstrings. Auth lives here rather than under routers/ because
# "who is this request from?" is a question every future feature asks.
