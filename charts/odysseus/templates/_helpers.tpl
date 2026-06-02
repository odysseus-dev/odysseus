{{/*
Expand the name of the chart.
*/}}
{{- define "odysseus.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Create a default fully qualified app name.
*/}}
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

{{- define "odysseus.labels" -}}
helm.sh/chart: {{ include "odysseus.chart" . }}
{{ include "odysseus.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "odysseus.selectorLabels" -}}
app.kubernetes.io/name: {{ include "odysseus.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "odysseus.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "odysseus.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "odysseus.image" -}}
{{- $tag := default .Chart.AppVersion .Values.image.tag -}}
{{- printf "%s:%s" .Values.image.repository $tag -}}
{{- end -}}

{{- define "odysseus.secretName" -}}
{{- if .Values.secretEnv.existingSecret -}}
{{- .Values.secretEnv.existingSecret -}}
{{- else -}}
{{- printf "%s-env" (include "odysseus.fullname" .) -}}
{{- end -}}
{{- end -}}

{{- define "odysseus.persistenceClaimName" -}}
{{- if .Values.persistence.existingClaim -}}
{{- .Values.persistence.existingClaim -}}
{{- else -}}
{{- include "odysseus.fullname" . -}}
{{- end -}}
{{- end -}}

{{- define "odysseus.chromadbName" -}}
{{- printf "%s-chromadb" (include "odysseus.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "odysseus.searxngName" -}}
{{- printf "%s-searxng" (include "odysseus.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "odysseus.ntfyName" -}}
{{- printf "%s-ntfy" (include "odysseus.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "odysseus.envVars" -}}
{{- range $key, $value := .Values.env }}
- name: {{ $key }}
  value: {{ $value | quote }}
{{- end }}
{{- if .Values.searxng.enabled }}
- name: SEARXNG_INSTANCE
  value: "http://{{ include "odysseus.searxngName" . }}:{{ .Values.searxng.service.port }}"
{{- end }}
{{- if .Values.chromadb.enabled }}
- name: CHROMADB_HOST
  value: {{ include "odysseus.chromadbName" . | quote }}
- name: CHROMADB_PORT
  value: {{ .Values.chromadb.service.port | quote }}
{{- end }}
{{- end -}}
