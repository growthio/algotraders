import styles from './Header.module.css';

export default function Header({ meta }) {
  const { symbol, expiry, spotPrice, lotSize, timestamp } = meta;

  return (
    <header className={styles.header}>
      <div className={styles.left}>
        <span className={styles.symbol}>{symbol}</span>
        <span className={styles.tag}>OPTION CHAIN</span>
      </div>
      <div className={styles.center}>
        <div className={styles.spotBlock}>
          <span className={styles.spotLabel}>SPOT</span>
          <span className={styles.spotPrice}>
            <span className={styles.upArrow}>▲</span>
            {spotPrice.toLocaleString('en-IN')}
          </span>
        </div>
      </div>
      <div className={styles.right}>
        <div className={styles.metaItem}>
          <span className={styles.metaLabel}>EXPIRY</span>
          <span className={styles.metaValue}>{expiry}</span>
        </div>
        <div className={styles.metaItem}>
          <span className={styles.metaLabel}>LOT SIZE</span>
          <span className={styles.metaValue}>{lotSize}</span>
        </div>
        <div className={styles.metaItem}>
          <span className={styles.metaLabel}>AS OF</span>
          <span className={styles.metaValue}>{timestamp}</span>
        </div>
      </div>
    </header>
  );
}
