/**
 * Re-applies precision fix to @polymarket/clob-client after install.
 * Upstream uses rawMakerAmt.toString() which keeps float dust; CLOB API requires
 * maker USDC max 2 decimals and taker outcome max 4 on buys (reversed on sells).
 */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(__dirname, "..");
const helpersPath = path.join(
  root,
  "node_modules/@polymarket/clob-client/dist/order-builder/helpers.js",
);

if (!fs.existsSync(helpersPath)) {
  console.warn("patch-clob-client-amounts: helpers.js missing, skip");
  process.exit(0);
}

let code = fs.readFileSync(helpersPath, "utf8");
if (code.includes("makerDp")) {
  console.log("patch-clob-client-amounts: already applied");
  process.exit(0);
}

const blockA = `export const buildOrderCreationArgs = async (signer, maker, signatureType, userOrder, roundConfig) => {
    const { side, rawMakerAmt, rawTakerAmt } = getOrderRawAmounts(userOrder.side, userOrder.size, userOrder.price, roundConfig);
    const makerAmount = parseUnits(rawMakerAmt.toString(), COLLATERAL_TOKEN_DECIMALS).toString();
    const takerAmount = parseUnits(rawTakerAmt.toString(), COLLATERAL_TOKEN_DECIMALS).toString();`;

const blockANew = `export const buildOrderCreationArgs = async (signer, maker, signatureType, userOrder, roundConfig) => {
    const { side, rawMakerAmt, rawTakerAmt } = getOrderRawAmounts(userOrder.side, userOrder.size, userOrder.price, roundConfig);
    const makerDp = userOrder.side === Side.BUY ? 2 : 4;
    const takerDp = userOrder.side === Side.BUY ? 4 : 2;
    const makerAmount = parseUnits(Number(rawMakerAmt.toFixed(makerDp)).toFixed(makerDp), COLLATERAL_TOKEN_DECIMALS).toString();
    const takerAmount = parseUnits(Number(rawTakerAmt.toFixed(takerDp)).toFixed(takerDp), COLLATERAL_TOKEN_DECIMALS).toString();`;

const blockB = `export const buildMarketOrderCreationArgs = async (signer, maker, signatureType, userMarketOrder, roundConfig) => {
    const { side, rawMakerAmt, rawTakerAmt } = getMarketOrderRawAmounts(userMarketOrder.side, userMarketOrder.amount, userMarketOrder.price || 1, roundConfig);
    const makerAmount = parseUnits(rawMakerAmt.toString(), COLLATERAL_TOKEN_DECIMALS).toString();
    const takerAmount = parseUnits(rawTakerAmt.toString(), COLLATERAL_TOKEN_DECIMALS).toString();`;

const blockBNew = `export const buildMarketOrderCreationArgs = async (signer, maker, signatureType, userMarketOrder, roundConfig) => {
    const { side, rawMakerAmt, rawTakerAmt } = getMarketOrderRawAmounts(userMarketOrder.side, userMarketOrder.amount, userMarketOrder.price || 1, roundConfig);
    const makerDp = userMarketOrder.side === Side.BUY ? 2 : 4;
    const takerDp = userMarketOrder.side === Side.BUY ? 4 : 2;
    const makerAmount = parseUnits(Number(rawMakerAmt.toFixed(makerDp)).toFixed(makerDp), COLLATERAL_TOKEN_DECIMALS).toString();
    const takerAmount = parseUnits(Number(rawTakerAmt.toFixed(takerDp)).toFixed(takerDp), COLLATERAL_TOKEN_DECIMALS).toString();`;

if (!code.includes(blockA)) {
  console.warn(
    "patch-clob-client-amounts: buildOrderCreationArgs block not found; upstream changed?",
  );
  process.exit(0);
}
if (!code.includes(blockB)) {
  console.warn(
    "patch-clob-client-amounts: buildMarketOrderCreationArgs block not found; upstream changed?",
  );
  process.exit(0);
}

code = code.replace(blockA, blockANew);
code = code.replace(blockB, blockBNew);
fs.writeFileSync(helpersPath, code);
console.log("patch-clob-client-amounts: patched helpers.js");
