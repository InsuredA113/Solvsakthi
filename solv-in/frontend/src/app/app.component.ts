import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpClient, HttpClientModule, HttpHeaders, HttpParams } from '@angular/common/http';
import { FormsModule } from '@angular/forms';
import { environment } from '../environments/environment';

interface User { full_name: string; email: string; role: string; is_active?: boolean; }
interface Claim {
  claim_id: string; customer_name: string; email?: string; phone?: string;
  policy_number: string; claim_type: 'Property' | 'Casualty'; incident_date: string;
  incident_description: string; loss_location: string; estimated_loss?: number | null;
  currency: string; status: string; created_at?: string; updated_at?: string;
}
interface ClaimDraft {
  customer_name: string; email: string; phone: string; policy_number: string;
  claim_type: 'Property' | 'Casualty'; incident_date: string; incident_description: string;
  loss_location: string; estimated_loss: number | null; currency: string;
}

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule, HttpClientModule, FormsModule],
  templateUrl: './app.component.html'
})
export class AppComponent implements OnInit {
  private http = inject(HttpClient);
  isAuthenticated = false;
  currentUser: User | null = null;
  loginEmail = '';
  loginPassword = '';
  loginBusy = false;
  activePage = 'Dashboard';
  apiStatus = 'Checking API…';
  loadingClaims = false;
  savingClaim = false;
  errorMessage = '';
  successMessage = '';
  searchTerm = '';
  typeFilter = '';
  statusFilter = '';
  claims: Claim[] = [];
  users: User[] = [];
  roles: string[] = [];
  userBusy = false;
  newUser = { full_name: '', email: '', password: '', role: 'Claims Manager' };
  totalClaims = 0;
  newClaims = 0;
  investigatingClaims = 0;
  pendingClaims = 0;
  closedClaims = 0;
  readonly navigation = ['Dashboard', 'Claims', 'New FNOL', 'Reports', 'Users', 'Settings'];
  readonly statuses = ['FNOL Received', 'Under Review', 'Investigation', 'Assessment', 'Pending Approval', 'Approved', 'Closed'];
  draft: ClaimDraft = this.emptyDraft();

  ngOnInit(): void {
    const token = sessionStorage.getItem('solvin_access_token');
    if (token) {
      this.http.get<User>(`${environment.apiBaseUrl}/auth/me`, { headers: this.authHeaders() }).subscribe({
        next: user => { this.currentUser = user; this.isAuthenticated = true; this.afterLogin(); },
        error: () => this.logout(false)
      });
    } else {
      this.checkApi();
    }
  }

  private authHeaders(): HttpHeaders {
    const token = sessionStorage.getItem('solvin_access_token') || '';
    return new HttpHeaders({ Authorization: `Bearer ${token}` });
  }

  checkApi(): void {
    this.http.get<{status: string}>(`${environment.apiBaseUrl}/health`).subscribe({
      next: result => this.apiStatus = `API ${result.status}`,
      error: () => this.apiStatus = 'API unavailable – start the FastAPI server'
    });
  }

  login(): void {
    this.errorMessage = '';
    this.loginBusy = true;
    this.http.post<{access_token: string; user: User}>(`${environment.apiBaseUrl}/auth/login`, {
      email: this.loginEmail, password: this.loginPassword
    }).subscribe({
      next: result => {
        sessionStorage.setItem('solvin_access_token', result.access_token);
        this.currentUser = result.user;
        this.isAuthenticated = true;
        this.loginPassword = '';
        this.loginBusy = false;
        this.afterLogin();
      },
      error: error => {
        this.loginBusy = false;
        this.errorMessage = error.error?.detail || 'Unable to sign in. Check the API and your credentials.';
      }
    });
  }

  private afterLogin(): void {
    this.apiStatus = 'Signed in';
    this.loadDashboard();
    this.loadClaims();
    if (this.isAdmin()) this.loadUsers();
  }

  logout(showMessage = true): void {
    sessionStorage.removeItem('solvin_access_token');
    this.currentUser = null;
    this.isAuthenticated = false;
    this.activePage = 'Dashboard';
    this.claims = [];
    this.users = [];
    this.errorMessage = '';
    this.successMessage = showMessage ? 'You have signed out.' : '';
    this.checkApi();
  }

  isAdmin(): boolean {
    return this.currentUser?.role === 'Super Administrator';
  }

  emptyDraft(): ClaimDraft {
    return {
      customer_name: '', email: '', phone: '', policy_number: '',
      claim_type: 'Property', incident_date: '', incident_description: '',
      loss_location: '', estimated_loss: null, currency: 'INR'
    };
  }

  selectPage(page: string): void {
    if (page === 'Users' && !this.isAdmin()) return;
    this.activePage = page;
    this.errorMessage = '';
    this.successMessage = '';
    if (page === 'Claims') this.loadClaims();
    if (page === 'Users') this.loadUsers();
  }

  loadDashboard(): void {
    this.http.get<Record<string, any>>(`${environment.apiBaseUrl}/dashboard/summary`, { headers: this.authHeaders() }).subscribe({
      next: data => {
        this.totalClaims = data['total_claims'] ?? 0;
        this.newClaims = data['new_claims'] ?? 0;
        this.investigatingClaims = data['under_investigation'] ?? 0;
        this.pendingClaims = data['pending_approval'] ?? 0;
        this.closedClaims = data['settled_claims'] ?? 0;
      },
      error: error => this.handleApiError(error)
    });
  }

  loadClaims(): void {
    this.loadingClaims = true;
    let params = new HttpParams();
    if (this.searchTerm.trim()) params = params.set('search', this.searchTerm.trim());
    if (this.typeFilter) params = params.set('claim_type', this.typeFilter);
    if (this.statusFilter) params = params.set('status', this.statusFilter);
    this.http.get<{items: Claim[]; count: number}>(`${environment.apiBaseUrl}/claims`, { params, headers: this.authHeaders() }).subscribe({
      next: result => { this.claims = result.items; this.loadingClaims = false; },
      error: error => { this.loadingClaims = false; this.handleApiError(error); this.claims = []; }
    });
  }

  submitFnol(): void {
    this.errorMessage = '';
    this.successMessage = '';
    if (!this.draft.customer_name.trim() || !this.draft.policy_number.trim() ||
        !this.draft.incident_date || !this.draft.incident_description.trim() ||
        !this.draft.loss_location.trim()) {
      this.errorMessage = 'Complete all required fields before submitting the FNOL.';
      return;
    }
    this.savingClaim = true;
    this.http.post<Claim>(`${environment.apiBaseUrl}/claims`, this.draft, { headers: this.authHeaders() }).subscribe({
      next: claim => {
        this.savingClaim = false;
        this.successMessage = `Claim ${claim.claim_id} was created successfully.`;
        this.draft = this.emptyDraft();
        this.loadClaims();
        this.loadDashboard();
      },
      error: error => { this.savingClaim = false; this.handleApiError(error); }
    });
  }

  changeStatus(claim: Claim, status: string): void {
    if (!status || status === claim.status) return;
    this.http.patch<Claim>(`${environment.apiBaseUrl}/claims/${encodeURIComponent(claim.claim_id)}/status`, { status }, { headers: this.authHeaders() }).subscribe({
      next: updated => {
        this.claims = this.claims.map(item => item.claim_id === updated.claim_id ? updated : item);
        this.loadDashboard();
      },
      error: error => this.handleApiError(error)
    });
  }

  loadUsers(): void {
    if (!this.isAdmin()) return;
    this.http.get<{items: User[]; count: number}>(`${environment.apiBaseUrl}/users`, { headers: this.authHeaders() }).subscribe({
      next: result => this.users = result.items,
      error: error => this.handleApiError(error)
    });
    this.http.get<{roles: string[]}>(`${environment.apiBaseUrl}/users/roles`, { headers: this.authHeaders() }).subscribe({
      next: result => this.roles = result.roles,
      error: error => this.handleApiError(error)
    });
  }

  createUser(): void {
    this.errorMessage = '';
    this.successMessage = '';
    this.userBusy = true;
    this.http.post<User>(`${environment.apiBaseUrl}/users`, this.newUser, { headers: this.authHeaders() }).subscribe({
      next: user => {
        this.userBusy = false;
        this.successMessage = `User ${user.email} created successfully.`;
        this.newUser = { full_name: '', email: '', password: '', role: 'Claims Manager' };
        this.loadUsers();
      },
      error: error => { this.userBusy = false; this.handleApiError(error); }
    });
  }

  private handleApiError(error: any): void {
    if (error.status === 401) {
      this.logout(false);
      this.errorMessage = 'Your session expired. Please sign in again.';
    } else {
      this.errorMessage = error.error?.detail || 'The request could not be completed.';
    }
  }

  formatAmount(amount: number | null | undefined, currency: string): string {
    if (amount === null || amount === undefined) return 'Not provided';
    try { return new Intl.NumberFormat('en-IN', { style: 'currency', currency }).format(amount); }
    catch { return `${currency} ${amount.toLocaleString('en-IN')}`; }
  }
}
