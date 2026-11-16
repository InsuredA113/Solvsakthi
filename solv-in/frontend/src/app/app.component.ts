import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpClient, HttpClientModule, HttpParams } from '@angular/common/http';
import { FormsModule } from '@angular/forms';
import { environment } from '../environments/environment';

interface Claim {
  claim_id: string;
  customer_name: string;
  email?: string;
  phone?: string;
  policy_number: string;
  claim_type: 'Property' | 'Casualty';
  incident_date: string;
  incident_description: string;
  loss_location: string;
  estimated_loss?: number | null;
  currency: string;
  status: string;
  created_at?: string;
  updated_at?: string;
}

interface ClaimDraft {
  customer_name: string;
  email: string;
  phone: string;
  policy_number: string;
  claim_type: 'Property' | 'Casualty';
  incident_date: string;
  incident_description: string;
  loss_location: string;
  estimated_loss: number | null;
  currency: string;
}

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule, HttpClientModule, FormsModule],
  templateUrl: './app.component.html'
})
export class AppComponent implements OnInit {
  private http = inject(HttpClient);
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
  totalClaims = 0;
  newClaims = 0;
  investigatingClaims = 0;
  pendingClaims = 0;
  closedClaims = 0;

  readonly navigation = ['Dashboard', 'Claims', 'New FNOL', 'Reports', 'Settings'];
  readonly statuses = ['FNOL Received', 'Under Review', 'Investigation', 'Assessment', 'Pending Approval', 'Approved', 'Closed'];

  draft: ClaimDraft = this.emptyDraft();

  ngOnInit(): void {
    this.http.get<{status: string}>(`${environment.apiBaseUrl}/health`).subscribe({
      next: (result) => {
        this.apiStatus = `API ${result.status}`;
        this.loadDashboard();
        this.loadClaims();
      },
      error: () => this.apiStatus = 'API unavailable – start the FastAPI server'
    });
  }

  emptyDraft(): ClaimDraft {
    return {
      customer_name: '', email: '', phone: '', policy_number: '',
      claim_type: 'Property', incident_date: '', incident_description: '',
      loss_location: '', estimated_loss: null, currency: 'INR'
    };
  }

  selectPage(page: string): void {
    this.activePage = page;
    this.errorMessage = '';
    this.successMessage = '';
    if (page === 'Claims') this.loadClaims();
  }

  loadDashboard(): void {
    this.http.get<Record<string, any>>(`${environment.apiBaseUrl}/dashboard/summary`).subscribe({
      next: data => {
        this.totalClaims = data['total_claims'] ?? 0;
        this.newClaims = data['new_claims'] ?? 0;
        this.investigatingClaims = data['under_investigation'] ?? 0;
        this.pendingClaims = data['pending_approval'] ?? 0;
        this.closedClaims = data['settled_claims'] ?? 0;
      }
    });
  }

  loadClaims(): void {
    this.loadingClaims = true;
    let params = new HttpParams();
    if (this.searchTerm.trim()) params = params.set('search', this.searchTerm.trim());
    if (this.typeFilter) params = params.set('claim_type', this.typeFilter);
    if (this.statusFilter) params = params.set('status', this.statusFilter);
    this.http.get<{items: Claim[]; count: number}>(`${environment.apiBaseUrl}/claims`, { params }).subscribe({
      next: result => {
        this.claims = result.items;
        this.loadingClaims = false;
      },
      error: error => {
        this.loadingClaims = false;
        this.errorMessage = error.error?.detail || 'Unable to load claims. Check the API and MongoDB connection.';
        this.claims = [];
      }
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
    this.http.post<Claim>(`${environment.apiBaseUrl}/claims`, this.draft).subscribe({
      next: claim => {
        this.savingClaim = false;
        this.successMessage = `Claim ${claim.claim_id} was created successfully.`;
        this.draft = this.emptyDraft();
        this.loadClaims();
        this.loadDashboard();
      },
      error: error => {
        this.savingClaim = false;
        this.errorMessage = error.error?.detail || 'Claim could not be saved. Verify that MongoDB is running.';
      }
    });
  }

  changeStatus(claim: Claim, status: string): void {
    if (!status || status === claim.status) return;
    this.http.patch<Claim>(`${environment.apiBaseUrl}/claims/${encodeURIComponent(claim.claim_id)}/status`, { status }).subscribe({
      next: updated => {
        this.claims = this.claims.map(item => item.claim_id === updated.claim_id ? updated : item);
        this.loadDashboard();
      },
      error: error => this.errorMessage = error.error?.detail || 'Unable to update claim status.'
    });
  }

  formatAmount(amount: number | null | undefined, currency: string): string {
    if (amount === null || amount === undefined) return 'Not provided';
    try {
      return new Intl.NumberFormat('en-IN', { style: 'currency', currency }).format(amount);
    } catch {
      return `${currency} ${amount.toLocaleString('en-IN')}`;
    }
  }
}
