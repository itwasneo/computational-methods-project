/** Presentation-only fixtures, not runner records, measured results or API DTOs. */
export type Trial = {
  id: string;
  instance: string;
  cities: number;
  algorithm: string;
  implementation: string;
  outcome: "Success" | "Failed";
  cost: string;
  time: string;
  comparison: "Eligible" | "Blocked" | "Unavailable";
  explanation: string;
};

export const trials: Trial[] = [
  {
    id: "preview-01",
    instance: "Uniform / small",
    cities: 128,
    algorithm: "NN",
    implementation: "visited-scan-v1",
    outcome: "Success",
    cost: "10.8421",
    time: "0.0042",
    comparison: "Eligible",
    explanation:
      "Sample eligibility state; no comparison calculated.",
  },
  {
    id: "preview-02",
    instance: "Uniform / small",
    cities: 128,
    algorithm: "NN",
    implementation: "remaining-array-v1",
    outcome: "Success",
    cost: "10.8421",
    time: "0.0036",
    comparison: "Blocked",
    explanation:
      "Source provenance is unknown.",
  },
  {
    id: "preview-03",
    instance: "Clustered / small",
    cities: 256,
    algorithm: "NN",
    implementation: "visited-scan-v1",
    outcome: "Failed",
    cost: "—",
    time: "—",
    comparison: "Unavailable",
    explanation:
      "Execution failed; no solution or timing available.",
  },
  {
    id: "preview-04",
    instance: "Clustered / medium",
    cities: 1024,
    algorithm: "NN",
    implementation: "remaining-array-v1",
    outcome: "Success",
    cost: "24.1908",
    time: "0.0481",
    comparison: "Blocked",
    explanation:
      "No matching baseline for this workload.",
  },
];
