@echo off
REM Start the local Neo4j 5.26.30 server (native install, no Docker).
REM Usage: double-click this file, or run it from cmd:
REM   scripts\start_neo4j.cmd
REM Keep the window open while demoing. Press Ctrl+C to stop.
REM Bolt: 127.0.0.1:7687  /  Browser console: http://127.0.0.1:7474

set "JAVA_HOME=G:\DevEnv\Java-JDK17\jdk-17.0.1.12-hotspot"
cd /d "G:\neo4j\bin"

echo JAVA_HOME=%JAVA_HOME%
echo Starting Neo4j (foreground, Ctrl+C to stop)...
echo Wait for "Started." in the log, then open http://127.0.0.1:7474
call neo4j.bat console
