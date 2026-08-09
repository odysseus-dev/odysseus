export function definedTsSymbol(value: number): number {
  return value + 1;
}

export const useDefinedTsSymbol = definedTsSymbol(41);
export const deliberateTypeScriptError: number = "not a number";
