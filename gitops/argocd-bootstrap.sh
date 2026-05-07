#!/usr/bin/env bash
set -euo pipefail

echo "Installing ArgoCD..."
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

echo "Waiting for ArgoCD to be ready..."
kubectl wait --for=condition=available --timeout=120s deployment/argocd-server -n argocd

echo "Applying ArgoCD config..."
kubectl apply -f gitops/argocd-install.yml

echo "Registering PulsarOps application..."
kubectl apply -f gitops/argocd-app.yml

echo "Getting ArgoCD initial admin password..."
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d
echo ""

echo "ArgoCD UI: kubectl port-forward svc/argocd-server -n argocd 8080:443"
echo "Login: admin / (password above)"
