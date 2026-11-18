**free
// ---------------------------------------------------------------------------
// CUSTINQ - Customer inquiry
// Sample program for the IBM Bob + TOBi automated build loop.
// Uses display file CUSTDSP (qddssrc/custdsp.dspf) and the shared
// constants in qrpgleref/custconst.rpgleinc.
// ---------------------------------------------------------------------------
ctl-opt dftactgrp(*no) actgrp(*caller) option(*srcstmt: *nodebugio);

/copy 'qrpgleref/custconst.rpgleinc'

dcl-ds dspInd qualified;
  exit ind pos(3);
end-ds;

dcl-f CUSTDSP workstn indds(dspInd);

MSGTXT = 'Enter a customer number (try 1001, 1002 or 1003).';

dou dspInd.exit;
  exfmt CUSTSCR;
  if not dspInd.exit;
    lookupCustomer(CUSTNO);
  endif;
enddo;

*inlr = *on;
return;

// ---------------------------------------------------------------------------
// lookupCustomer - fill the screen fields for a customer number
// (demo data only - a real program would read a customer file)
// ---------------------------------------------------------------------------
dcl-proc lookupCustomer;
  dcl-pi *n;
    custNumber packed(7: 0) const;
  end-pi;

  select;
    when custNumber = 1001;
      CUSTNAME = 'Acme Manufacturing';
      CUSTSTS = STATUS_ACTIVE;
    when custNumber = 1002;
      CUSTNAME = 'Bluewater Logistics';
      CUSTSTS = STATUS_HOLD;
    when custNumber = 1003;
      CUSTNAME = 'Cedar Ridge Foods';
      CUSTSTS = STATUS_ACTIVE;
    other;
      CUSTNAME = *blanks;
      CUSTSTS = *blanks;
      MSGTXT = 'Customer ' + %char(custNumber) + ' was not found.';
      return;
  endsl;

  MSGTXT = *blanks;
end-proc;
