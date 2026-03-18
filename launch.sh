#!/bin/bash
exec claude --effort max --chrome "$(<prompt.txt)"
