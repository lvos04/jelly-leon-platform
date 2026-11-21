#!/bin/sh
# Elke container die de proef zelf start, draagt het label van deze proef. Zo ruimt de
# afsluitende stap van de job ook na een afgebroken proef precies deze containers op.
set -eu
case "${1-}" in
    run|create)
        opdracht=$1
        shift
        exec /usr/bin/docker "$opdracht" --label "nl.medialeon.ci.proef=${CI_PROEF:?}" "$@"
        ;;
esac
exec /usr/bin/docker "$@"
