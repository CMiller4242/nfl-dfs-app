"""
Anchors pytest's rootdir here so `import dfs_data_pipeline` and `import lib...`
resolve the same way they do when Streamlit runs the app from the repo root,
regardless of whether tests are invoked as `pytest` or `python -m pytest`.
"""
