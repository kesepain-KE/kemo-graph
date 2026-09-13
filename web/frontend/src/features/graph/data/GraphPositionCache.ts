import type { ForceSettings } from "../engine/layoutTypes";

const DATABASE_NAME = "kemo-graph-layout-cache";
const STORE_NAME = "layouts";
const DATABASE_VERSION = 1;
const MAX_LAYOUTS = 24;

type LayoutRecord = {
  key: string;
  nodeIds: string[];
  positions: ArrayBuffer;
  updatedAt: number;
};

export function makeLayoutCacheKey(
  revision: string,
  mode: "global" | "local",
  anchorId: string | null,
  depth: number,
  settings: ForceSettings,
  nodeScale = 1,
): string {
  const parameters = [
    settings.centerStrength,
    settings.repulsionStrength,
    settings.linkStrength,
    settings.linkDistance,
    settings.damping,
    settings.alphaDecay,
    settings.stableEnergy,
  ].join(",");
  return ["unbounded-v2", revision, mode, anchorId ?? "-", depth, nodeScale, parameters].join("|");
}

export async function readLayoutPositions(
  key: string,
  nodeIds: string[],
): Promise<Float32Array<ArrayBuffer> | null> {
  const database = await openDatabase();
  if (!database) return null;
  try {
    const record = await requestResult<LayoutRecord | undefined>(
      database.transaction(STORE_NAME, "readonly").objectStore(STORE_NAME).get(key),
    );
    if (
      !record
      || record.nodeIds.length !== nodeIds.length
      || record.nodeIds.some((nodeId, index) => nodeId !== nodeIds[index])
    ) {
      return null;
    }
    const positions = new Float32Array(record.positions.slice(0));
    return positions.length === nodeIds.length * 2 ? positions : null;
  } finally {
    database.close();
  }
}

export async function writeLayoutPositions(
  key: string,
  nodeIds: string[],
  positions: Float32Array<ArrayBufferLike>,
): Promise<void> {
  const database = await openDatabase();
  if (!database || positions.length !== nodeIds.length * 2) return;
  try {
    const transaction = database.transaction(STORE_NAME, "readwrite");
    const serializedPositions = new ArrayBuffer(positions.byteLength);
    new Uint8Array(serializedPositions).set(
      new Uint8Array(
        positions.buffer,
        positions.byteOffset,
        positions.byteLength,
      ),
    );
    transaction.objectStore(STORE_NAME).put({
      key,
      nodeIds: [...nodeIds],
      positions: serializedPositions,
      updatedAt: Date.now(),
    } satisfies LayoutRecord);
    await transactionDone(transaction);
    await pruneLayouts(database);
  } finally {
    database.close();
  }
}

async function openDatabase(): Promise<IDBDatabase | null> {
  if (!("indexedDB" in globalThis)) return null;
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE_NAME, DATABASE_VERSION);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(STORE_NAME)) {
        request.result.createObjectStore(STORE_NAME, { keyPath: "key" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("无法打开图谱布局缓存"));
    request.onblocked = () => reject(new Error("图谱布局缓存被其他页面占用"));
  });
}

async function pruneLayouts(database: IDBDatabase): Promise<void> {
  const records = await requestResult<LayoutRecord[]>(
    database.transaction(STORE_NAME, "readonly").objectStore(STORE_NAME).getAll(),
  );
  if (records.length <= MAX_LAYOUTS) return;
  const expired = records
    .sort((left, right) => right.updatedAt - left.updatedAt)
    .slice(MAX_LAYOUTS);
  const transaction = database.transaction(STORE_NAME, "readwrite");
  const store = transaction.objectStore(STORE_NAME);
  for (const record of expired) store.delete(record.key);
  await transactionDone(transaction);
}

function requestResult<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("图谱布局缓存操作失败"));
  });
}

function transactionDone(transaction: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(
      transaction.error ?? new Error("图谱布局缓存事务失败"),
    );
    transaction.onabort = () => reject(
      transaction.error ?? new Error("图谱布局缓存事务已取消"),
    );
  });
}
