**free
// ---------------------------------------------------------------------------
// ORDINQ - Order inquiry
// FIX-LOOP DEMO: this program contains deliberate compile errors.
// See demo/fix-loop/README.md.
// Uses display file ORDDSP (qddssrc/orddsp.dspf).
// ---------------------------------------------------------------------------
ctl-opt dftactgrp(*no) actgrp(*caller) option(*srcstmt: *nodebugio);

dcl-f ORDDSP workstn indds(dspInd);

dcl-ds dspInd qualified;
  exit ind pos(3);
end-ds;

dcl-c STATUS_OPEN 'OPEN';
dcl-c STATUS_SHIPPED 'SHIPPED';

MSGTXT = 'Enter an order number (try 5001 or 5002).';

dou dspInd.exit;
  exfmt ORDSCR;
  if not dspInd.exit;
    lookupOrder(ORDNO);
  endif;
enddo;

*inlr = *on;
return;

// ---------------------------------------------------------------------------
// lookupOrder - fill the screen fields for an order number
// (demo data only - a real program would read an order file)
// ---------------------------------------------------------------------------
dcl-proc lookupOrder;
  dcl-pi *n;
    orderNumber packed(7: 0) const;
  end-pi;

  select;
    when orderNumber = 5001;
      ORDCUST = 'Acme Manufacturing';
      ORDAMT = 1250.00;
      ORDSTS = STATUS_OPEN;
    when orderNumber = 5002;
      ORDCUS = 'Bluewater Logistics';
      ORDAMT = 87.45;
      ORDSTS = STATUS_SHIPPED;
    other;
      ORDCUST = *blanks;
      ORDAMT = 0;
      ORDSTS = *blanks;
      MSGTXT = 'Order ' + orderNumber + ' was not found.';
      return;
  endsl;

  MSGTXT = *blanks;
end-proc;
