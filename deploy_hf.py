"""Create/update the Hugging Face Space (Docker SDK) and upload app + data/out. Needs `hf auth login` first.

Usage: .venv/bin/python deploy_hf.py <hf-username>/<space-name>
Sets APERTUS_BASE_URL / APERTUS_MODEL as public Space variables. APERTUS_API_KEY is never handled here:
add it yourself under Space -> Settings -> Variables and secrets -> New secret."""
import sys
from pathlib import Path
from huggingface_hub import HfApi

repo = sys.argv[1]
api = HfApi()
api.create_repo(repo, repo_type="space", space_sdk="docker", exist_ok=True)
api.add_space_variable(repo, "APERTUS_BASE_URL", "https://gateway.keera.ch/api/v1")
api.add_space_variable(repo, "APERTUS_MODEL", "apertus-v1-5-70b")
api.upload_folder(repo_id=repo, repo_type="space", folder_path=".",
                  allow_patterns=["Dockerfile", ".dockerignore", "requirements.txt", "app.py",
                                  "static/*", "src/**", "data/out/*", "data/onboard/**"],
                  ignore_patterns=["**/__pycache__/**", "*.env", ".env"], commit_message="Deploy ArchAI web demo")
# a Space needs its YAML header at the top of README.md; GitHub's README stays clean
readme = Path("deploy/hf_space_header.md").read_text() + "\n" + Path("README.md").read_text()
api.upload_file(path_or_fileobj=readme.encode(), path_in_repo="README.md", repo_id=repo, repo_type="space")
print(f"https://huggingface.co/spaces/{repo}  ->  https://{repo.replace('/', '-').replace('_', '-').lower()}.hf.space")
