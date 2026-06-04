# Cookbook Download ETA Feature

## What was changed
* **`static/js/cookbookRunning.js`**: 
  - Added utility functions `_parseSizeToBytes` and `_formatEta` to convert human-readable sizes (like 1.81G) into raw bytes and convert seconds into a human-readable remaining time string.
  - Enhanced the `_pollBackgroundStatus` parser regex to capture the "total bytes" of the download alongside the currently downloaded bytes from `hf_transfer` and standard `tqdm` outputs.
  - Added a state tracker `el._etaHistory` which maintains a sliding window (15 seconds) of byte counts and their timestamps to accurately calculate the real-time download speed.
  - Added an `applyEta` helper to seamlessly inject the ETA calculation into the progress badge string (`text = applyEta(text, pct)`).
  - Handles stalls gracefully: when speed drops to 0, or if history gets cleared (e.g. jumping to a new shard), the UI will briefly show "Calculating..." or hold the last known ETA (for 30 seconds) until a steady speed is re-established, before handing over to the application's existing stall detection.

## What the feature does
It adds a live ETA display (e.g. "2 min 34 sec remaining") to Cookbook model downloads alongside the progress percentage and raw download speed. By calculating speed directly from the logged byte progress rather than trusting the somewhat jumpy console output, it provides a very stable time-remaining prediction for long model downloads.

## How to test it
1. Start the application locally.
2. Navigate to the Cookbook UI and select a large model (such as a 7B or 8B parameter model).
3. Click "Download" to fetch it.
4. Open the running tasks sidebar/tab.
5. You should immediately see the download status change to `Calculating...`, followed by the exact ETA once there are ~2 seconds of stable throughput data.
6. Temporarily pause or throttle your network to verify that the ETA reacts correctly or falls back appropriately if stalled.
