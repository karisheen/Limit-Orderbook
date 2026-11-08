import Terminal from './orderflow/components/Terminal';
import { NativeProvider } from './integration/NativeContext';

export default function App() {
  return <NativeProvider><Terminal /></NativeProvider>;
}
