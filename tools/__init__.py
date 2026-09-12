"""Deterministic tools for the eDNA metabarcoding curation pipeline.

Each module here is a pure, testable piece of logic (no LLM calls). The
harness/orchestrator calls into these; skills describe when/why a judgment
call should override or interpret their output.
"""
