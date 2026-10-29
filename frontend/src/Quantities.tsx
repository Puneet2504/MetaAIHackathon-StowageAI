import type { Allocation } from "./types/api";

// How each ordered quantity became tank volume: temperature, density and the volume correction.
// Values that were not in the order are marked "assumed" so the planner checks them against the documents.
export default function Quantities({ allocation }: { allocation: Allocation[] }) {
  const rows = allocation.filter((a) => a.conversion);
  if (!rows.length) return null;
  return (
    <div className="quantities">
      <h3 className="small">Quantities</h3>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Grade</th><th>Ordered</th><th>Temperature</th><th>Density at 15 °C</th>
              <th className="num" title="Volume correction factor (ASTM Table 54B)">VCF</th>
              <th className="num">m³ at 15 °C</th><th className="num">m³ in tanks</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((a) => {
              const c = a.conversion!;
              const tempAssumed = c.temp_source !== "order";
              const densityAssumed = !c.density_source.startsWith("order");
              return (
                <tr key={a.parcel}>
                  <td>{a.grade}</td>
                  <td>{c.ordered}</td>
                  <td className={tempAssumed ? "assumed" : undefined}>
                    {c.temp_c} °C <span className="muted small">{tempAssumed ? `assumed, ${c.temp_source}` : "given"}</span>
                  </td>
                  <td className={densityAssumed ? "assumed" : undefined}>
                    {c.density15.toFixed(4)} <span className="muted small">{densityAssumed ? "assumed, typical" : c.density_source.replace(/^order/, "given")}</span>
                  </td>
                  <td className="num">{c.vcf.toFixed(4)}</td>
                  <td className="num">{Math.round(c.volume15_m3).toLocaleString()}</td>
                  <td className="num"><strong>{Math.round(c.volume_m3).toLocaleString()}</strong></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="muted small">
        Tonnes are in air unless the order says in vacuum. Volumes use ASTM Table 54B; missing temperatures use the
        load port's average sea temperature. Check assumed values against the cargo documents.
      </p>
    </div>
  );
}
