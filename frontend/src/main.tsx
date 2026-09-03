import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import { PortalAuthProvider } from './contexts/PortalAuthContext';
import './index.css';
import './portal-fixes.css';
import './aigc.css';
import './aigc-actions.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <PortalAuthProvider>
        <App />
      </PortalAuthProvider>
    </BrowserRouter>
  </React.StrictMode>
);
