{{/*
Blocks pod start until <user> can log in to <db>. Logging in with the
consumer's own password also waits out a password change the setup hook
hasn't applied yet. Literal copies live in values*.yaml, keep them in step.
The image is Compose's postgres image, tests/deploy checks every copy.
*/}}
{{- define "srdp.waitForDbLogin" -}}
- name: wait-for-{{ .db }}-db
  image: postgres:18-alpine@sha256:77f585114c32fbca283dc835b0596f4e52b51b4c6662d7810b2f4084f60a1873
  command:
    - sh
    - -c
    - |
      for i in $(seq 1 60); do
        psql -tAc "SELECT 1" >/dev/null && exit 0
        echo "waiting for $PGUSER to log in to $PGDATABASE ($i/60)..."
        sleep 2
      done
      exit 1
  securityContext:
    runAsNonRoot: true
    runAsUser: 70
    allowPrivilegeEscalation: false
    readOnlyRootFilesystem: true
    capabilities:
      drop: [ALL]
  env:
    - name: PGHOST
      value: {{ .root.Values.global.postgresqlHost | quote }}
    - name: PGUSER
      value: {{ .user | quote }}
    - name: PGDATABASE
      value: {{ .db | quote }}
    - name: PGPASSWORD
      valueFrom:
        secretKeyRef:
          name: {{ .secretName }}
          key: {{ .secretKey }}
{{- end -}}

{{/*
DuckLake connects as the superuser, see DUCKLAKE_PG_* in srdp.ducklakeEnv.
*/}}
{{- define "srdp.waitForDucklakeDb" -}}
{{ include "srdp.waitForDbLogin" (dict "root" . "db" "ducklake" "user" "postgres" "secretName" "srdp-postgres" "secretKey" "postgres-password") }}
{{- end -}}

{{/*
DuckLake connection env for the apps that read the catalog, the Kubernetes
equivalent of the DUCKLAKE_* block on each app in docker-compose.yml.
*/}}
{{- define "srdp.ducklakeEnv" -}}
- name: DUCKLAKE_PG_HOST
  value: {{ .Values.global.postgresqlHost | quote }}
- name: DUCKLAKE_PG_USER
  value: postgres
- name: DUCKLAKE_PG_PASSWORD
  valueFrom:
    secretKeyRef:
      name: srdp-postgres
      key: postgres-password
- name: DUCKLAKE_PG_DB
  value: ducklake
- name: DUCKLAKE_DATA_PATH
  value: {{ .Values.ducklakeData.mountPath | quote }}
{{- end -}}

{{/*
Read-only mount of the shared DuckLake data volume (templates/ducklake-data-pvc.yaml),
same as the ducklake-data:/data/ducklake:ro mount of the Compose readers.
*/}}
{{- define "srdp.ducklakeVolumeMount" -}}
- name: ducklake-data
  mountPath: {{ .Values.ducklakeData.mountPath | quote }}
  readOnly: true
{{- end -}}

{{- define "srdp.ducklakeVolume" -}}
- name: ducklake-data
  persistentVolumeClaim:
    claimName: ducklake-data
{{- end -}}

{{/*
Image reference for an image this repo builds, prefixed with
global.srdpRegistry so one value moves every SRDP image to another registry.
Usage: {{ include "srdp.image" (list . .Values.api.image) }}
*/}}
{{- define "srdp.image" -}}
{{- $root := index . 0 -}}
{{- $image := index . 1 -}}
{{- $registry := ternary $root.Values.global.platformRegistry $root.Values.global.srdpRegistry (default false $image.platform) -}}
{{- $tag := $image.tag | default $root.Values.global.imageTag | default $root.Chart.AppVersion -}}
{{- printf "%s/%s:%s" (trimSuffix "/" $registry) $image.repository $tag | quote -}}
{{- end -}}

{{/*
Pod-level imagePullSecrets from global.imagePullSecrets, empty when unset.
*/}}
{{- define "srdp.imagePullSecrets" -}}
{{- with .Values.global.imagePullSecrets }}
imagePullSecrets:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{/*
Pod annotation for pods that read a localSecrets Secret. A secretKeyRef does
not change the pod spec, so without it a pod keeps the old value after a
redeploy, e.g. Marquez after srdp-setup resets its role's password. Hashes
the whole local-secrets.yaml, so any local Secret change rolls every reader.
Kind only: External Secrets (#69) covers this in the cloud. Subcharts get the
same annotation from the Justfile's local_secrets_args. Zitadel is left out on
purpose: nothing resets its role's password until #78, so a rolled Zitadel pod
would fail to log in with a changed password.
*/}}
{{- define "srdp.localSecretsChecksum" -}}
{{- if .Values.localSecrets.enabled -}}
annotations:
  checksum/local-secrets: {{ include (print .Template.BasePath "/local-secrets.yaml") . | sha256sum }}
{{- end -}}
{{- end -}}
