{{/* Base name, overridable. */}}
{{- define "odysseus.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Full release-qualified name. */}}
{{- define "odysseus.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "odysseus.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Common labels. */}}
{{- define "odysseus.labels" -}}
helm.sh/chart: {{ include "odysseus.chart" . }}
{{ include "odysseus.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{/* Base selector labels (no component). */}}
{{- define "odysseus.selectorLabels" -}}
app.kubernetes.io/name: {{ include "odysseus.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/* Per-component names. */}}
{{- define "odysseus.app.fullname" -}}{{ include "odysseus.fullname" . }}{{- end -}}
{{- define "odysseus.chromadb.fullname" -}}{{ include "odysseus.fullname" . }}-chromadb{{- end -}}
{{- define "odysseus.searxng.fullname" -}}{{ include "odysseus.fullname" . }}-searxng{{- end -}}
{{- define "odysseus.ntfy.fullname" -}}{{ include "odysseus.fullname" . }}-ntfy{{- end -}}

{{/* ServiceAccount name. */}}
{{- define "odysseus.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "odysseus.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/* odysseus app Secret name (created or existing). */}}
{{- define "odysseus.app.secretName" -}}
{{- if .Values.odysseus.secrets.existingSecret -}}
{{- .Values.odysseus.secrets.existingSecret -}}
{{- else -}}
{{- printf "%s-secrets" (include "odysseus.app.fullname" .) -}}
{{- end -}}
{{- end -}}
