import styles from './OptionChainTable.module.css';

const CALL_COLS = ['OI', 'CHNG OI', '% CHNG OI', 'VOLUME', 'IV', 'LTP', 'CHNG LTP', '% CHNG LTP', 'BUY QTY', 'SELL QTY', 'BID QTY', 'BID', 'ASK QTY', 'ASK'];
const PUT_COLS = ['ASK', 'ASK QTY', 'BID', 'BID QTY', 'SELL QTY', 'BUY QTY', '% CHNG LTP', 'CHNG LTP', 'LTP', 'IV', 'VOLUME', '% CHNG OI', 'CHNG OI', 'OI'];

export default function TableHeader() {
  return (
    <thead>
      <tr>
        <th
          colSpan={14}
          className={styles.sectionHeaderCall}
        >
          CALL
        </th>
        <th className={styles.sectionHeaderStrike}>STRIKE</th>
        <th
          colSpan={14}
          className={styles.sectionHeaderPut}
        >
          PUT
        </th>
      </tr>
      <tr>
        {CALL_COLS.map((col) => (
          <th key={col} className={styles.subHeaderCall}>
            {col}
          </th>
        ))}
        <th className={styles.subHeaderStrike}>STRIKE</th>
        {PUT_COLS.map((col) => (
          <th key={col} className={styles.subHeaderPut}>
            {col}
          </th>
        ))}
      </tr>
    </thead>
  );
}
