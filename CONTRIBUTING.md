# Contributing

Merci de contribuer a PatchworkAgent ! Ce guide explique comment ajouter un nouveau provider IA, modifier l'orchestrateur, ou corriger un bug.

---

## Pre-requis

- Docker
- k3s (ou un cluster Kubernetes local)
- `kubectl` configure sur le namespace `ai-bot`
- Python 3.11+ (pour l'orchestrateur)
- Les cles API des providers que vous souhaitez tester

---

## Structure du projet

```
.
├── app/
│   ├── app.py                  # Orchestrateur FastAPI
│   └── requirements.txt
├── providers/
│   ├── git_workflow.sh          # Logique Git partagee (clone, branch, push, PR)
│   ├── claude_code.sh           # Provider Claude Code
│   ├── openai.sh                # Provider OpenAI Codex
│   └── aider.sh                 # Provider Aider (OpenRouter)
├── images/
│   ├── orchestrator/Dockerfile  # Image orchestrateur
│   ├── worker-claude/           # Image + run.sh worker Claude
│   ├── worker-codex/            # Image + run.sh worker Codex
│   └── worker-aider/            # Image + run.sh worker Aider
├── k8s/
│   ├── namespace-rbac.yaml      # Namespace + RBAC
│   ├── networkpolicy.yaml       # Politiques reseau
│   ├── orchestrator.yaml        # Deployment + Service + Ingress
│   ├── ai-issue-*.yaml          # Jobs manuels par provider
│   ├── debug-*.yaml             # Jobs de debug par provider
│   └── secrets/                 # Templates de secrets (pas de valeurs)
├── ansible/
│   ├── playbook.yml             # Deploiement VPS complet
│   ├── inventory.ini            # Inventaire par defaut
│   ├── inventory-local.ini      # Inventaire local
│   ├── inventory-prod.ini       # Inventaire production (gitignored)
│   ├── requirements.yml         # Collections Ansible
│   └── group_vars/vps.yml
├── docs/
│   ├── catalog-info.yaml        # Backstage service catalog
│   └── workspace.dsl            # Architecture C4 (Structurizr)
├── .github/
│   └── workflows/secret-scan.yml
├── CONTRIBUTING.md
├── SECURITY.md
└── LICENSE (MIT)
```

---

## Ajouter un nouveau provider IA

Pour ajouter un provider `myprovider` avec le label de declenchement `ai-pr-myprovider` :

### 1. Script provider : `providers/myprovider.sh`

Suivre le pattern existant :

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 1. Verifier le token GitHub
: "${GITHUB_TOKEN:?GITHUB_TOKEN is required}"

# 2. Clone & branch (logique partagee)
source "$SCRIPT_DIR/git_workflow.sh"
git_clone_and_branch

# 3. Appeler le CLI IA
myprovider-cli run "Fix issue #${ISSUE_NUMBER}: ${GITHUB_ISSUE_TITLE:-no title}. ..."

# 4. Push & PR (logique partagee)
git_push_and_pr "Automated PR created by MyProvider for issue #${ISSUE_NUMBER}."
```

Les fonctions `git_clone_and_branch` et `git_push_and_pr` sont dans `providers/git_workflow.sh`. Ne pas dupliquer cette logique.

### 2. Wrapper d'entree : `images/worker-myprovider/run.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

echo "=== worker start ==="
echo "TIME: $(date -u --iso-8601=seconds)"
echo "AI_PROVIDER=${AI_PROVIDER:-myprovider}"
echo "GITHUB_REPO=${GITHUB_REPO:-}"
echo "GITHUB_ISSUE_NUMBER=${GITHUB_ISSUE_NUMBER:-}"
echo "GITHUB_INSTALLATION_ID=${GITHUB_INSTALLATION_ID:-}"
if [[ "${DEBUG_ENV:-0}" == "1" ]]; then
  echo "---- env (whitelist) ----"
  printenv | grep -E '^(AI_PROVIDER|GITHUB_REPO|GITHUB_ISSUE_NUMBER|GITHUB_INSTALLATION_ID|NAMESPACE|JOB_IMAGE|HOME|PATH)=' || true
  echo "---- end env ----"
fi

exec /app/providers/myprovider.sh
```

### 3. Dockerfile : `images/worker-myprovider/Dockerfile`

```dockerfile
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
WORKDIR /tmp

RUN apt-get update && apt-get install -y \
    curl ca-certificates git bash jq procps \
 && rm -rf /var/lib/apt/lists/*

# Installer les deps specifiques au CLI (Node.js, Go, etc.)

# Creer un user non-root (certains CLIs le requierent)
RUN useradd -m -s /bin/bash worker
USER worker

# Installer le CLI IA
RUN curl -fsSL https://example.com/install.sh | bash

# Copier les scripts
WORKDIR /app
COPY --chown=worker:worker images/worker-myprovider/run.sh /app/run.sh
COPY --chown=worker:worker providers/ /app/providers/
RUN sed -i 's/\r$//' /app/run.sh /app/providers/*.sh \
 && chmod +x /app/run.sh /app/providers/*.sh

ENV PATH="/home/worker/.local/bin:${PATH}"
WORKDIR /work

ENTRYPOINT ["/app/run.sh"]
CMD []
```

### 4. Job de debug : `k8s/debug-myprovider.yaml`

Creer un Job qui :
- Verifie l'installation du CLI (`command -v`, `--version`)
- Verifie la cle API (longueur, presence)
- Execute un test simple (ex: "Quelle est la capitale de la France ?")
- Dort ensuite (`sleep 36000`) pour permettre `kubectl exec`

### 5. Enregistrer le provider dans `app/app.py`

```python
# Ajouter la variable d'image
MYPROVIDER_WORKER_IMAGE = os.getenv("MYPROVIDER_WORKER_IMAGE", "worker-myprovider:latest")

# Ajouter dans PROVIDER_CONFIG
"myprovider": ProviderConfig(
    image=MYPROVIDER_WORKER_IMAGE,
    ai_provider="myprovider",
    api_secret=ProviderSecretRef("MY_API_KEY", "myprovider-api-key", "MY_API_KEY"),
),
```

### 6. Mettre a jour le README.md

- Table des secrets (section 1)
- Table des images (section 2)
- Commandes de build (section 2)
- Section debug jobs (section 3)
- Section "En cas de changement d'image" (section 4)
- Troubleshooting si necessaire (section 5)

### 7. Verification

```shell
# Syntaxe Python
python -c "import ast; ast.parse(open('app/app.py').read())"

# Build de l'image
docker build -f images/worker-myprovider/Dockerfile -t worker-myprovider:latest .

# Import dans k3s
docker save worker-myprovider:latest | sudo k3s ctr images import -

# Lancer le job de debug
kubectl -n ai-bot apply -f k8s/debug-myprovider.yaml
kubectl -n ai-bot logs -f job/debug-myprovider

# Test end-to-end : ajouter le label ai-pr-myprovider sur une issue
```

---

## Modifier l'orchestrateur

L'orchestrateur est dans `app/app.py` (FastAPI).

```shell
# Verifier la syntaxe apres modification
python -c "import ast; ast.parse(open('app/app.py').read())"

# Rebuild + deploiement
docker build -f images/orchestrator/Dockerfile -t ghcr.io/hey-intent/orchestrator:latest .
docker save ghcr.io/hey-intent/orchestrator:latest | sudo k3s ctr images import -
kubectl -n ai-bot rollout restart deployment/orchestrator

# Verifier les logs
kubectl -n ai-bot logs -f deploy/orchestrator --tail=200
```

---

## Conventions

### Scripts shell

- Shebang : `#!/usr/bin/env bash`
- Toujours `set -euo pipefail`
- Fin de ligne Unix (LF). Les Dockerfiles font `sed -i 's/\r$//'` par securite.
- Ne jamais logger de secrets. Utiliser `GIT_ASKPASS` pour les tokens Git.

### Dockerfiles

- Base : `ubuntu:22.04`
- Deps communes : `curl ca-certificates git bash jq procps`
- User non-root `worker` (UID auto)
- `WORKDIR /work` pour l'execution
- `ENTRYPOINT ["/app/run.sh"]`

### Secrets Kubernetes

- Un secret par provider (isolation : compromission d'un secret n'affecte pas les autres)
- Injection via `secretKeyRef` (jamais en clair dans les manifests)
- Convention de nommage : `<provider>-api-key`

### Nommage

| Element | Convention | Exemple |
|---------|-----------|---------|
| Label GitHub | `ai-pr-<provider>` | `ai-pr-claude` |
| Image Docker | `worker-<provider>:latest` | `worker-aider:latest` |
| Secret K8s | `<service>-api-key` | `openrouter-api-key` |
| Script provider | `providers/<provider>.sh` | `providers/aider.sh` |
| Job de debug | `k8s/debug-<provider>.yaml` | `k8s/debug-aider.yaml` |
| Cle dans `PROVIDER_CONFIG` | `<provider>` (suffixe du label) | `"aider"` |

---

## Limitations connues (POC)

Ce projet est un POC. Les contributions pour adresser ces limitations sont bienvenues :

- Concurrence / idempotence (doubles declenchements, collisions de branches/jobs)
- Timeout / cancellation des jobs (pods "zombies")
- Gestion des conflits Git / PR deja existante
- Monitoring / alerting (Prometheus, Grafana)
- Dashboard de suivi des jobs
- Gestion des quotas et du budget tokens par PR / par repo
- Rate limiting sur les webhooks
- Retry / dead-letter queue en cas d'echec
- Traitement des images dans les issues (screenshots, diagrammes)
- Gestion des commentaires dans les issues (contexte additionnel, instructions de suivi)
- Support multi-cluster / haute disponibilite
