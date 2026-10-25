/** Fictional persona used across tests. Never replace with real data. */
export const alexProfile = {
  schemaVersion: 1,
  name: { first: "Alex", last: "Example" },
  email: "alex@example.com",
  phone: { e164: "+15555550100", country: "US" },
  address: { line1: "123 Example St", city: "Springfield", region: "XX", postal: "00000", country: "US" },
  links: { github: "https://github.com/example" },
  education: [
    { school: "Example State University", degree: "BS", major: "Biology", gpa: 3.5, start: "2023-09", end: "2027-06", expected: true },
  ],
  workAuth: {
    citizenships: ["US"],
    countries: { US: { authorized: true, basis: "citizen", sponsorshipNow: false, sponsorshipFuture: false } },
  },
};
