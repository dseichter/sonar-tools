#!/bin/bash
#
# sonar-tools
# Copyright (C) 2021-2024 Olivier Korach
# mailto:olivier.korach AT gmail DOT com
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 3 of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program; if not, write to the Free Software Foundation,
# Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
#
set -euo pipefail

cur_dir=$(dirname $0)

function logmsg {
    echo $* | tee -a $IT_LOG_FILE
}

function run_test {
    file=$1; shift
    logmsg "$@"
    $@
    check "$file"
}
function run_test_stdout {
    file=$1; shift
    logmsg "$@" ">$file"
    $@ >$file
    check "$file"
}

check() {
    if [ -s "$1" ]; then
        logmsg "Output file $1 is OK"
    else
        logmsg "Output file $1 is missing or empty"
        # exit 1
    fi
}


[ $# -eq 0 ] && echo "Usage: $0 <env1> [... <envN>]" && exit 1

IT_ROOT="$( cd -- "$(dirname "$0")" >/dev/null 2>&1 ; cd .. ; pwd -P )"
IT_ROOT="$IT_ROOT/tmp"
IT_LOG_FILE="$IT_ROOT/it.log"
mkdir -p $IT_ROOT
rm -f $IT_ROOT/*.log $IT_ROOT/*.csv $IT_ROOT/*.json

noExport=0
if [ "$1" == "--noExport" ]; then
    noExport=1
    shift
fi

date | tee -a $IT_LOG_FILE
for env in $*
do

    echo "Install sonar-tools current local version" | tee -a $IT_LOG_FILE
    ./deploy.sh nodoc

    id="it$$"
    logmsg "Running with environment $env - sonarId $id"
    sonar create --id $id --tag $env --port 6000 --pg_port 5999 --pg_backup ~/backup/db.$env.backup

    export SONAR_TOKEN=$SONAR_TOKEN_ADMIN_USER
    export SONAR_HOST_URL="http://localhost:6000"
    logmsg "IT $env sonar-measures-export"

    f="$IT_ROOT/measures-$env-unrel.csv"; run_test $f sonar-measures-export -b -f $f -m _main --withURL
    f="$IT_ROOT/measures-$env-2.csv";     run_test_stdout $f sonar-measures-export -b -m _main --withURL
    f="$IT_ROOT/measures-$env-3.csv";     run_test_stdout $f sonar-measures-export -b -p -r -d -m _all

    f="$IT_ROOT/measures-$env-1.json";    run_test $f sonar-measures-export -b -f $f -m _all
    f="$IT_ROOT/measures-$env-2.json";    run_test_stdout $f sonar-measures-export -b -p -r -d -m _all --format json
    f="$IT_ROOT/measures-$env-3.csv";     run_test $f sonar-measures-export -b -f $f --csvSeparator '+' -m _main

    logmsg "IT $env sonar-findings-export"

    f="$IT_ROOT/findings-$env-unrel.csv";  run_test $f sonar-findings-export -v DEBUG -f $f
    f="$IT_ROOT/findings-$env-1.json";     run_test $f sonar-findings-export -f $f
    f="$IT_ROOT/findings-$env-2.json";     run_test_stdout $f sonar-findings-export -v DEBUG --format json -k okorach_audio-video-tools,okorach_sonar-tools
    f="$IT_ROOT/findings-$env-3.json";     run_test_stdout $f sonar-findings-export -v DEBUG --format json -k okorach_audio-video-tools,okorach_sonar-tools --useFindings
    f="$IT_ROOT/findings-$env-4.csv";      run_test_stdout $f sonar-findings-export --format json -k okorach_audio-video-tools,okorach_sonar-tools --csvSeparator '+'
    
    logmsg "IT $env sonar-audit"
    f="$IT_ROOT/audit-$env-unrel.csv";     run_test_stdout $f sonar-audit
    f="$IT_ROOT/audit-$env-1.json";        run_test $f sonar-audit -f $f
    f="$IT_ROOT/audit-$env-2.json";        run_test_stdout $f sonar-audit --format json --what qualitygates,qualityprofiles,settings
    f="$IT_ROOT/audit-$env-3.csv";         run_test_stdout $f sonar-audit  --csvSeparator '+' --format csv

    logmsg "IT $env sonar-housekeeper"
    f="$IT_ROOT/housekeeper-$env-1.csv";   run_test_stdout $f sonar-housekeeper -P 365 -B 90 -T 180 -R 30

    logmsg "IT $env sonar-loc"
    f="$IT_ROOT/loc-$env-1.csv";           run_test_stdout $f sonar-loc
    f="$IT_ROOT/loc-$env-unrel.csv";       run_test_stdout $f sonar-loc -n -a
    f="$IT_ROOT/loc-$env-2.csv";           run_test $f sonar-loc -n -a -f $f --csvSeparator ';'

    logmsg "sonar-rules $env"
    f="$IT_ROOT/rules-$env-1.csv";         run_test_stdout $f sonar-rules -e
    f="$IT_ROOT/rules-$env-2.csv";         run_test $f sonar-rules -e -f $f
    f="$IT_ROOT/rules-$env-3.json";        run_test_stdout $f sonar-rules -e --format json
    f="$IT_ROOT/rules-$env-4.json";        run_test $f sonar-rules -e -f $f

    logmsg "sonar-config $env"
    f="$IT_ROOT/config-$env-1.json";       run_test_stdout $f sonar-config -e -w "qualitygates, qualityprofiles, projects" -k okorach_audio-video-tools,okorach_sonar-tools
    f="$IT_ROOT/config-$env-2.json";       run_test_stdout $f sonar-config --export
    f="$IT_ROOT/config-$env-unrel.json";   run_test $f sonar-config --export -f $f

    if [ $noExport -eq 1 ]; then
        logmsg "sonar-projects-export $env test skipped"
    else
        logmsg "sonar-projects-export $env"
        sonar-projects-export
    fi

    logmsg "sonar-findings-export $env ADMIN export"
    f1="$IT_ROOT/findings-$env-admin.csv";   run_test $f1 sonar-findings-export -v DEBUG -f $f1 -k okorach_audio-video-tools,okorach_sonar-tools

    logmsg "sonar-findings-export $env USER export"
    export SONAR_TOKEN=$SONAR_TOKEN_USER_USER
    f2="$IT_ROOT/findings-$env-user.csv";    run_test $f2 sonar-findings-export -v DEBUG -f $f2 -k okorach_audio-video-tools,okorach_sonar-tools

    # Restore admin token as long as previous version is 2.9 or less
    logmsg "Restore sonar-tools last released version"
    echo "Y" | pip uninstall sonar-tools
    pip install sonar-tools
    
    export SONAR_TOKEN=$SONAR_TOKEN_ADMIN_USER
    logmsg "IT released tools $env"
    sonar-measures-export -b -f $IT_ROOT/measures-$env-rel.csv -m _main --withURL
    sonar-findings-export -f $IT_ROOT/findings-$env-rel.csv
    sonar-audit >$IT_ROOT/audit-$env-rel.csv || echo "OK"
    sonar-loc -n -a >$IT_ROOT/loc-$env-rel.csv 
    sonar-config -e >$IT_ROOT/config-$env-rel.json 

    echo "IT compare released and unreleased $env" | tee -a $IT_LOG_FILE
    for f in measures findings audit loc
    do
        root=$IT_ROOT/$f-$env
        logmsg "=========================="
        logmsg $f-$env diff
        logmsg "=========================="
        sort $root-rel.csv >$root-rel.sorted.csv
        sort $root-unrel.csv >$root-unrel.sorted.csv
        diff $root-rel.sorted.csv $root-unrel.sorted.csv | tee -a $IT_LOG_FILE || echo "" 
    done
    for f in config
    do
        root=$IT_ROOT/$f-$env
        logmsg "=========================="
        logmsg $f-$env diff
        logmsg "=========================="
        diff $root-rel.json $root-unrel.json | tee -a $IT_LOG_FILE || echo "" 
    done
    logmsg "=========================="
    logmsg findings-$env admin vs user diff
    logmsg "=========================="
    f1="$IT_ROOT/findings-$env-admin.csv"
    f2="$IT_ROOT/findings-$env-user.csv"
    diff $f1 $f2 | tee -a $IT_LOG_FILE || echo ""

    id=$(cd test;ls |grep "-sonar"|cut -d '-' -f 1)
    logmsg "Deleting environment sonarId $id"
    sonar delete --id $id
done

logmsg "====================================="
logmsg "          IT tests success"
logmsg "====================================="
