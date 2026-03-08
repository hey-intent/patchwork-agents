workspace "PatchworkAgent" "Kubernetes-native system that automatically solves GitHub issues using AI code agents" {

    model {
        # --- Personas ---
        developer = person "Developer" "Opens GitHub issues and reviews AI-generated pull requests"

        # --- External Systems ---
        github = softwareSystem "GitHub" "Source code hosting, issue tracking, PR management, webhook delivery" "External"
        anthropicApi = softwareSystem "Anthropic API" "Claude LLM provider" "External"
        openaiApi = softwareSystem "OpenAI API" "Codex LLM provider" "External"
        openrouterApi = softwareSystem "OpenRouter API" "Multi-model gateway" "External"

        # --- Main System ---
        aiPrBot = softwareSystem "PatchworkAgent" "Receives GitHub issue webhooks, spawns ephemeral AI workers on Kubernetes to generate code fixes and create pull requests" {

            orchestrator = container "Orchestrator" "FastAPI service that receives webhooks, validates signatures, generates ephemeral GitHub installation tokens, creates per-job token Secrets, and creates Kubernetes Jobs" "Python 3.12 / FastAPI / Uvicorn" "Service"

            workerClaude = container "Claude Worker" "Ephemeral K8s Job that clones a repo, invokes Claude Code CLI to fix the issue, pushes a branch, and creates a PR" "Ubuntu 22.04 / Bash / Claude CLI" "Worker"
            workerCodex = container "Codex Worker" "Ephemeral K8s Job that clones a repo, invokes OpenAI Codex CLI to fix the issue, pushes a branch, and creates a PR" "Ubuntu 22.04 / Node.js 22 / Codex CLI" "Worker"
            workerAider = container "Aider Worker" "Ephemeral K8s Job that clones a repo, invokes Aider CLI (OpenRouter) to fix the issue, pushes a branch, and creates a PR" "Python 3.12 / Bash / Aider CLI" "Worker"

            k8sApi = container "Kubernetes API" "k3s API Server - manages Jobs, Secrets, RBAC, NetworkPolicies in the ai-bot namespace" "k3s" "Infrastructure"
            secrets = container "K8s Secrets" "Stores GitHub App PEM, webhook secret, API keys (Anthropic, OpenAI, OpenRouter), admin token, and ephemeral per-job GITHUB_TOKEN secrets (ownerReference to Job, auto-cleaned)" "Kubernetes Secrets" "Datastore"

            gitWorkflow = container "Git Workflow Library" "Shared Bash library (git_workflow.sh) used by all workers for clone, branch, push, and PR creation" "Bash" "Library"
        }

        # --- Relationships: Developer ---
        developer -> github "Opens issues, adds labels (ai-pr-*), reviews PRs"

        # --- Relationships: GitHub <-> System ---
        github -> orchestrator "Sends issue webhook (POST /webhook/github)" "HTTPS / JSON"
        orchestrator -> github "Generates ephemeral installation tokens" "HTTPS / GitHub App JWT (RS256)"

        workerClaude -> github "Clones repo, pushes branch, creates PR" "HTTPS / git + gh CLI"
        workerCodex -> github "Clones repo, pushes branch, creates PR" "HTTPS / git + gh CLI"
        workerAider -> github "Clones repo, pushes branch, creates PR" "HTTPS / git + curl"

        # --- Relationships: Orchestrator <-> K8s ---
        orchestrator -> k8sApi "Creates Jobs (batch/v1), creates/patches/deletes Secrets" "Kubernetes Python Client"
        k8sApi -> workerClaude "Schedules and runs Job pod" "Container Runtime"
        k8sApi -> workerCodex "Schedules and runs Job pod" "Container Runtime"
        k8sApi -> workerAider "Schedules and runs Job pod" "Container Runtime"
        k8sApi -> secrets "Reads/writes secrets" "K8s API"

        orchestrator -> secrets "Reads GitHub App PEM, webhook secret, admin config" "K8s API (env injection)"
        workerClaude -> secrets "Reads ANTHROPIC_API_KEY + ephemeral GITHUB_TOKEN Secret" "K8s env from Secret"
        workerCodex -> secrets "Reads OPENAI_API_KEY + ephemeral GITHUB_TOKEN Secret" "K8s env from Secret"
        workerAider -> secrets "Reads OPENROUTER_API_KEY + ephemeral GITHUB_TOKEN Secret" "K8s env from Secret"

        # --- Relationships: Workers <-> AI Providers ---
        workerClaude -> anthropicApi "Sends code generation requests" "Claude Code CLI / HTTPS"
        workerCodex -> openaiApi "Sends code generation requests" "Codex CLI / HTTPS"
        workerAider -> openrouterApi "Sends code generation requests" "Aider CLI / HTTPS"

        # --- Relationships: Workers <-> Git Workflow ---
        workerClaude -> gitWorkflow "Sources git_workflow.sh for git operations"
        workerCodex -> gitWorkflow "Sources git_workflow.sh for git operations"
        workerAider -> gitWorkflow "Sources git_workflow.sh for git operations"

        # --- Deployment ---
        prodEnv = deploymentEnvironment "Production" {
            deploymentNode "k3s Cluster" "Single-node Kubernetes (k3s)" "Linux" {
                deploymentNode "ai-bot namespace" "Isolated namespace with RBAC" "Kubernetes Namespace" {

                    deploymentNode "Orchestrator Deployment" "1 replica, Service ClusterIP, exposed via Ingress (/webhook/github) or temporary tunnel for tests" "Kubernetes Deployment" {
                        orchInstance = containerInstance orchestrator
                    }

                    deploymentNode "Worker Jobs (ephemeral)" "TTL 3600s, backoffLimit 0, restartPolicy Never" "Kubernetes Job" {
                        claudeInstance = containerInstance workerClaude
                        codexInstance = containerInstance workerCodex
                        aiderInstance = containerInstance workerAider
                    }

                    deploymentNode "Secrets Store" "" "Kubernetes Secrets" {
                        secretsInstance = containerInstance secrets
                    }
                }
            }

            deploymentNode "GitHub Cloud" "github.com" "SaaS" {
                ghInstance = softwareSystemInstance github
            }

            deploymentNode "AI Provider Cloud" "External APIs" "SaaS" {
                anthropicInstance = softwareSystemInstance anthropicApi
                openaiInstance = softwareSystemInstance openaiApi
                openrouterInstance = softwareSystemInstance openrouterApi
            }
        }
    }

    views {
        # --- Level 1: System Context ---
        systemContext aiPrBot "SystemContext" "High-level view of PatchworkAgent and its external dependencies" {
            include *
            autoLayout
        }

        # --- Level 2: Container ---
        container aiPrBot "Containers" "Internal architecture of the PatchworkAgent system" {
            include *
            autoLayout
        }

        # --- Deployment View ---
        deployment aiPrBot "Production" "Deployment" "How the system is deployed on k3s" {
            include *
            autoLayout
        }

        # --- Dynamic: Issue Resolution Flow ---
        dynamic aiPrBot "IssueResolutionFlow" "End-to-end flow when a developer labels an issue with ai-pr-claude" {
            developer -> github "1. Adds label 'ai-pr-claude' to issue"
            github -> orchestrator "2. Webhook POST /webhook/github"
            orchestrator -> secrets "3. Reads GitHub App PEM + webhook secret"
            orchestrator -> github "4. Generates installation token (JWT -> installation token)"
            orchestrator -> k8sApi "5. Creates ephemeral Secret with GITHUB_TOKEN"
            orchestrator -> k8sApi "6. Creates Job (ai-pr-*-claude)"
            k8sApi -> workerClaude "7. Schedules worker pod"
            workerClaude -> secrets "8. Reads ANTHROPIC_API_KEY + ephemeral GITHUB_TOKEN"
            workerClaude -> github "9. Clones repository"
            workerClaude -> anthropicApi "10. Invokes Claude Code CLI for fix"
            workerClaude -> github "11. Pushes branch + creates PR"
            developer -> github "12. Reviews and merges PR"
            autoLayout
        }

        styles {
            element "Person" {
                shape Person
                background #08427B
                color #ffffff
                description true
            }
            element "Software System" {
                background #1168BD
                color #ffffff
                shape RoundedBox
                description true
            }
            element "External" {
                background #999999
                color #ffffff
                description true
            }
            element "Container" {
                background #438DD5
                color #ffffff
                description true
            }
            element "Service" {
                background #438DD5
                color #ffffff
                description true
            }
            element "Worker" {
                background #438DD5
                color #ffffff
                description true
            }
            element "Infrastructure" {
                shape Cylinder
                background #438DD5
                color #ffffff
                description true
            }
            element "Datastore" {
                shape Cylinder
                background #438DD5
                color #ffffff
                description true
            }
            element "Library" {
                shape Component
                background #85BBF0
                color #ffffff
                description true
            }
            relationship "Relationship" {
                thickness 2
                color #707070
            }
        }

        properties {
            "structurizr.legend" "true"
        }
    }

}
