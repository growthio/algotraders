import { useMemo } from 'react';
import Header from './components/Header/Header';
import SummaryPanel from './components/SummaryPanel/SummaryPanel';
import OptionChainTable from './components/OptionChainTable/OptionChainTable';
import { OPTION_CHAIN_DATA } from './data/dummyData';
import { computeSummary, computeOCProfile } from './utils/calculations';
import styles from './App.module.css';

export default function App() {
  const { rows, meta } = OPTION_CHAIN_DATA;

  const summary = useMemo(
    () => computeSummary(rows, meta.spotPrice),
    [rows, meta.spotPrice]
  );

  const ocProfile = useMemo(
    () => computeOCProfile(rows, meta.spotPrice),
    [rows, meta.spotPrice]
  );

  return (
    <div className={styles.app}>
      <Header meta={meta} />
      <SummaryPanel summary={summary} ocProfile={ocProfile} />
      <OptionChainTable
        rows={rows}
        spotPrice={meta.spotPrice}
        summary={summary}
      />
    </div>
  );
}
