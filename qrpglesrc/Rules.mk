# RPGLE programs. Listing CUSTDSP.FILE as a prerequisite makes TOBi build the
# display file first, and lets /ibmi-build-object recompile CUSTINQ whenever
# custdsp.dspf changes (avoids level-check errors at run time).
# Copybook dependencies (/COPY qrpgleref/...) are found by scanning the source.
CUSTINQ.PGM: custinq.pgm.rpgle CUSTDSP.FILE
# Order inquiry program (FIX-LOOP DEMO - contains deliberate errors)
ORDINQ.PGM: ordinq.pgm.rpgle ORDDSP.FILE
