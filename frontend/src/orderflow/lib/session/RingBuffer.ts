// Adapted from zadescoxp/order-flow-screener (Apache-2.0), revision 2b451b5.
// Modified for Limit Orderbook: Vite integration, strict typing and local workspace.
export class RingBuffer<T> {
  private readonly values: T[];
  private readonly capacity: number;
  private cursor = 0;
  private length = 0;

  constructor(capacity: number) {
    this.capacity = capacity;
    this.values = new Array<T>(capacity);
  }

  push(value: T): void {
    this.values[this.cursor] = value;
    this.cursor = (this.cursor + 1) % this.capacity;
    this.length = Math.min(this.length + 1, this.capacity);
  }

  clear(): void {
    this.cursor = 0;
    this.length = 0;
  }

  newestFirst(): T[] {
    return Array.from({ length: this.length }, (_, index) => {
      const position = (this.cursor - 1 - index + this.capacity) % this.capacity;
      // Only positions initialized by push are included in this.length.
      return this.values[position]!;
    });
  }
}
