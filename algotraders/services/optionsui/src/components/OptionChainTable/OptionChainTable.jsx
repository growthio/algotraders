import { useMemo } from 'react';
import styles from './OptionChainTable.module.css';
import TableHeader from './TableHeader';
import StrikeRow from './StrikeRow';
import { getTop3ByField } from '../../utils/calculations';
import { getPctChngOIColorScale } from '../../utils/colorUtils';

const PCT_CHNG_OI_ROW_MIN = 10;
const PCT_CHNG_OI_ROW_MAX = 30;

export default function OptionChainTable({ rows, spotPrice }) {
  const atmIndex = useMemo(
    () => rows.findIndex((r) => r.strike === spotPrice),
    [rows, spotPrice]
  );

  const { top3CallOI, top3CallChngOI, top3CallVol, top3PutOI, top3PutChngOI, top3PutVol } =
    useMemo(() => {
      const otmCallRows = rows.slice(atmIndex + 1);
      const otmPutRows = rows.slice(0, atmIndex);
      return {
        top3CallOI: getTop3ByField(otmCallRows, (r) => r.call.oi),
        top3CallChngOI: getTop3ByField(otmCallRows, (r) => r.call.chngOI),
        top3CallVol: getTop3ByField(otmCallRows, (r) => r.call.volume),
        top3PutOI: getTop3ByField(otmPutRows, (r) => r.put.oi),
        top3PutChngOI: getTop3ByField(otmPutRows, (r) => r.put.chngOI),
        top3PutVol: getTop3ByField(otmPutRows, (r) => r.put.volume),
      };
    }, [rows, atmIndex]);

  const { makeCallPctScale, makePutPctScale } = useMemo(() => {
    const callPctValues = rows.slice(PCT_CHNG_OI_ROW_MIN, PCT_CHNG_OI_ROW_MAX + 1).map((r) => r.call.pctChngOI);
    const putPctValues = rows.slice(PCT_CHNG_OI_ROW_MIN, PCT_CHNG_OI_ROW_MAX + 1).map((r) => r.put.pctChngOI);
    const callMin = Math.min(...callPctValues);
    const callMax = Math.max(...callPctValues);
    const putMin = Math.min(...putPctValues);
    const putMax = Math.max(...putPctValues);
    return {
      makeCallPctScale: (value) => getPctChngOIColorScale(value, callMin, callMax),
      makePutPctScale: (value) => getPctChngOIColorScale(value, putMin, putMax),
    };
  }, [rows]);

  const isInPctRange = (index) => index >= PCT_CHNG_OI_ROW_MIN && index <= PCT_CHNG_OI_ROW_MAX;

  return (
    <div className={styles.tableWrapper}>
      <table className={styles.table}>
        <TableHeader />
        <tbody>
          {rows.map((row, index) => {
            const isATM = row.strike === spotPrice;
            const inRange = isInPctRange(index);
            return (
              <StrikeRow
                key={row.strike}
                rowData={row}
                rowIndex={index}
                isATM={isATM}
                top3CallOI={top3CallOI}
                top3CallChngOI={top3CallChngOI}
                top3CallVol={top3CallVol}
                top3PutOI={top3PutOI}
                top3PutChngOI={top3PutChngOI}
                top3PutVol={top3PutVol}
                pctCallChngOIScale={inRange ? makeCallPctScale : null}
                pctPutChngOIScale={inRange ? makePutPctScale : null}
              />
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
