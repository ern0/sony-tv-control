#!/bin/bash
clear

BASE=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

alias a='clear ; $BASE/sony_web_tv.py'
alias s='$BASE/launch.sh'

cd $BASE
basename $BASE
echo -ne "\033]0;`basename $BASE`\007"
