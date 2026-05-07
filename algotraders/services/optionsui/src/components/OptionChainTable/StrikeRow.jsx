import { getTop3Color, getIVStyle } from '../../utils/colorUtils';
import { formatIndian, formatIV, formatLTP, formatPct } from '../../utils/calculations';
import styles from './OptionChainTable.module.css';

function mergeStyle(base, override) {
  if (!override) return base;
  return { ...base, ...override };
}

function PctCell({ value, baseStyle }) {
  const color = value > 0 ? '#22C55E' : value < 0 ? '#EF4444' : '#94a3b8';
  return (
    <td style={{ ...baseStyle, color }}>
      {formatPct(value)}
    </td>
  );
}

function ChngCell({ value, baseStyle }) {
  const color = value > 0 ? '#22C55E' : value < 0 ? '#EF4444' : '#94a3b8';
  return (
    <td style={{ ...baseStyle, color }}>
      {value > 0 ? '+' : ''}{value.toFixed(2)}
    </td>
  );
}

function getRank(top3, strike) {
  for (let i = 0; i < top3.length; i++) {
    if (top3[i]?.strike === strike) return i + 1;
  }
  return 0;
}

export default function StrikeRow({
  rowData,
  rowIndex,
  isATM,
  top3CallOI,
  top3CallChngOI,
  top3CallVol,
  top3PutOI,
  top3PutChngOI,
  top3PutVol,
  pctCallChngOIScale,
  pctPutChngOIScale,
}) {
  const { strike, call, put } = rowData;

  const isITMCall = rowIndex <= 20;
  const isITMPut = rowIndex >= 21;

  const callBaseStyle = isITMCall
    ? { background: '#1F497D', color: '#FFFFFF' }
    : { background: '#FFFFFF', color: '#1a1a2e' };

  const putBaseStyle = isITMPut
    ? { background: '#1F497D', color: '#FFFFFF' }
    : { background: '#FFFFFF', color: '#1a1a2e' };

  const strikeStyle = {
    background: isATM ? '#FBBF24' : '#4F81BD',
    color: isATM ? '#000000' : '#FFFFFF',
    fontWeight: 'bold',
    borderLeft: isATM ? '3px solid #F59E0B' : undefined,
    borderRight: isATM ? '3px solid #F59E0B' : undefined,
  };

  const atmRowClass = isATM ? styles.atmRow : '';

  const callOIRank = getRank(top3CallOI, strike);
  const callChngOIRank = getRank(top3CallChngOI, strike);
  const callVolRank = getRank(top3CallVol, strike);
  const putOIRank = getRank(top3PutOI, strike);
  const putChngOIRank = getRank(top3PutChngOI, strike);
  const putVolRank = getRank(top3PutVol, strike);

  const callOIStyle = mergeStyle(callBaseStyle, getTop3Color('call', callOIRank));
  const callChngOIStyle = mergeStyle(callBaseStyle, getTop3Color('call', callChngOIRank));
  const callVolStyle = mergeStyle(callBaseStyle, getTop3Color('call', callVolRank));
  const putOIStyle = mergeStyle(putBaseStyle, getTop3Color('put', putOIRank));
  const putChngOIStyle = mergeStyle(putBaseStyle, getTop3Color('put', putChngOIRank));
  const putVolStyle = mergeStyle(putBaseStyle, getTop3Color('put', putVolRank));

  const callIVStyle = mergeStyle(callBaseStyle, getIVStyle(call.iv));
  const putIVStyle = mergeStyle(putBaseStyle, getIVStyle(put.iv));

  const callPctChngOIStyle = pctCallChngOIScale
    ? pctCallChngOIScale(call.pctChngOI)
    : callBaseStyle;

  const putPctChngOIStyle = pctPutChngOIScale
    ? pctPutChngOIScale(put.pctChngOI)
    : putBaseStyle;

  const td = styles.td;

  return (
    <tr className={`${styles.strikeRow} ${atmRowClass}`}>
      <td style={callOIStyle} className={td}>{formatIndian(call.oi)}</td>
      <td style={callChngOIStyle} className={td}>{formatIndian(call.chngOI)}</td>
      <td style={callPctChngOIStyle} className={td}>{formatPct(call.pctChngOI)}</td>
      <td style={callVolStyle} className={td}>{formatIndian(call.volume)}</td>
      <td style={callIVStyle} className={td}>{formatIV(call.iv)}</td>
      <td style={callBaseStyle} className={td}>{formatLTP(call.ltp)}</td>
      <ChngCell value={call.chngLTP} baseStyle={callBaseStyle} />
      <PctCell value={call.pctChngLTP} baseStyle={callBaseStyle} />
      <td style={callBaseStyle} className={td}>{formatIndian(call.buyQty)}</td>
      <td style={callBaseStyle} className={td}>{formatIndian(call.sellQty)}</td>
      <td style={callBaseStyle} className={td}>{formatIndian(call.bidQty)}</td>
      <td style={callBaseStyle} className={td}>{formatLTP(call.bid)}</td>
      <td style={callBaseStyle} className={td}>{formatIndian(call.askQty)}</td>
      <td style={callBaseStyle} className={td}>{formatLTP(call.ask)}</td>

      <td style={strikeStyle} className={`${td} ${styles.strikeCell}`}>
        {strike.toLocaleString('en-IN')}
      </td>

      <td style={putBaseStyle} className={td}>{formatLTP(put.ask)}</td>
      <td style={putBaseStyle} className={td}>{formatIndian(put.askQty)}</td>
      <td style={putBaseStyle} className={td}>{formatLTP(put.bid)}</td>
      <td style={putBaseStyle} className={td}>{formatIndian(put.bidQty)}</td>
      <td style={putBaseStyle} className={td}>{formatIndian(put.sellQty)}</td>
      <td style={putBaseStyle} className={td}>{formatIndian(put.buyQty)}</td>
      <PctCell value={put.pctChngLTP} baseStyle={putBaseStyle} />
      <ChngCell value={put.chngLTP} baseStyle={putBaseStyle} />
      <td style={putBaseStyle} className={td}>{formatLTP(put.ltp)}</td>
      <td style={putIVStyle} className={td}>{formatIV(put.iv)}</td>
      <td style={putVolStyle} className={td}>{formatIndian(put.volume)}</td>
      <td style={putPctChngOIStyle} className={td}>{formatPct(put.pctChngOI)}</td>
      <td style={putChngOIStyle} className={td}>{formatIndian(put.chngOI)}</td>
      <td style={putOIStyle} className={td}>{formatIndian(put.oi)}</td>
    </tr>
  );
}
