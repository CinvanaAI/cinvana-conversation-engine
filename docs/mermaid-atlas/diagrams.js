import { systemDiagrams } from "./diagrams-system.js";
import { stageDiagrams } from "./diagrams-stages.js";
import { reliabilityDiagrams } from "./diagrams-reliability.js";
import { implementationDiagrams } from "./diagrams-implementation.js";

export const diagrams = [
  ...systemDiagrams,
  ...stageDiagrams,
  ...reliabilityDiagrams,
  ...implementationDiagrams,
].sort((left, right) => left.id.localeCompare(right.id));
