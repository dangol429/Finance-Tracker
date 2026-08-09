# Marks `app/` as a Python package.
#
# Why this empty file matters: it's what makes `from app.models import User`
# resolve. Without it, `app` is just a folder and every import in the project
# breaks. It stays empty on purpose — putting code here would run it on *any*
# `app.*` import, which is a subtle way to create import cycles and slow startup.
