"""
OpenClaw Status — LLM-powered release assessment.
Single package: collect → analyze → render → publish.
"""

# The app's own version (distinct from every "version" elsewhere in the code, which
# refers to the assessed OpenClaw *product*). Kept in sync with config.APP_VERSION by
# a test; bump on release and cut the matching annotated git tag from it.
__version__ = "1.0.0"
