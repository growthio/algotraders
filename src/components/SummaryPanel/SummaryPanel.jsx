import styles from './SummaryPanel.module.css';
import { getPCRStyle, getRatioStyle } from '../../utils/colorUtils';
import { formatIndian } from '../../utils/calculations';

const PURPLE_BG = { background: '#8064A2' };
const PURPLE_TEXT = { color: '#ffffff' };

function StatBlock({ label, value, bgStyle, textStyle }) {
  return (
    <div className={styles.statBlock} style={bgStyle}>
      <div className={styles.statValue} style={textStyle}>{value}</div>
      <div className={styles.statLabel} style={textStyle ? { ...textStyle, opacity: 0.75 } : undefined}>{label}</div>
    </div>
  );
}

function SideSummary({ title, headerStyle, totalOI, totalChngOI, nearChngOI, totalVol, oiRatios, chngOIRatios, volDiff }) {
  const oiRatioColor = getRatioStyle(oiRatios.r2_1);
  const chngOIRatioColor = getRatioStyle(chngOIRatios.r2_1);

  return (
    <div className={styles.sidePanel}>
      <div className={styles.sidePanelHeader} style={headerStyle}>{title}</div>
      <div className={styles.statsRow}>
        <StatBlock label="Total OI" value={formatIndian(totalOI)} />
        <StatBlock label="Chng in OI" value={formatIndian(totalChngOI)} bgStyle={PURPLE_BG} textStyle={PURPLE_TEXT} />
        <StatBlock label="Near Chng in OI" value={formatIndian(nearChngOI)} bgStyle={PURPLE_BG} textStyle={PURPLE_TEXT} />
        <StatBlock label="Total Volume" value={formatIndian(totalVol)} />
        <StatBlock label="OI Ratio" value={oiRatios.r2_1.toFixed(3)} bgStyle={{ background: oiRatioColor.background }} textStyle={{ color: oiRatioColor.color }} />
        <StatBlock label="Chng in OI Ratio" value={chngOIRatios.r2_1.toFixed(3)} bgStyle={{ background: chngOIRatioColor.background }} textStyle={{ color: chngOIRatioColor.color }} />
        <StatBlock label="Vol Difference" value={formatIndian(volDiff.top1_2)} />
      </div>
    </div>
  );
}

export default function SummaryPanel({ summary, ocProfile }) {
  const pcrStyle = getPCRStyle(summary.pcr);
  const pcrLabel = summary.pcr > 1.1 ? 'BULLISH' : summary.pcr < 0.9 ? 'BEARISH' : 'NEUTRAL';

  return (
    <div className={styles.summaryPanel}>
      <SideSummary
        title="CALL SUMMARY"
        headerStyle={{ background: '#1F497D', color: '#FFFFFF' }}
        totalOI={summary.totalCallOI}
        totalChngOI={summary.totalCallChngOI}
        nearChngOI={summary.nearCallChngOI}
        totalVol={summary.totalCallVol}
        oiRatios={ocProfile.callOIRatios}
        chngOIRatios={ocProfile.callChngOIRatios}
        volDiff={ocProfile.callVolDiff}
      />

      <div className={styles.pcrCenter}>
        <div className={styles.pcrCard} style={pcrStyle}>
          <div className={styles.pcrLabel}>PUT-CALL RATIO</div>
          <div className={styles.pcrValue}>{summary.pcr.toFixed(3)}</div>
          <div className={styles.pcrSignal}>{pcrLabel}</div>
        </div>
      </div>

      <SideSummary
        title="PUT SUMMARY"
        headerStyle={{ background: '#4F81BD', color: '#FFFFFF' }}
        totalOI={summary.totalPutOI}
        totalChngOI={summary.totalPutChngOI}
        nearChngOI={summary.nearPutChngOI}
        totalVol={summary.totalPutVol}
        oiRatios={ocProfile.putOIRatios}
        chngOIRatios={ocProfile.putChngOIRatios}
        volDiff={ocProfile.putVolDiff}
      />
    </div>
  );
}
