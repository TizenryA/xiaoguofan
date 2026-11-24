package api

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/Mag1cFall/AIStudio2API/internal/aistudio"
)

func TestAccountScopeMiddleware(t *testing.T) {
	var seen []string
	var scoped bool
	next := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		seen, scoped = aistudio.ContextAccountScope(r.Context())
		if r.Header.Get(AccountScopeHeader) != "" {
			t.Fatal("scope header must not leak downstream")
		}
		w.WriteHeader(http.StatusNoContent)
	})
	required := accountScopeMiddleware(true, next)
	recorder := httptest.NewRecorder()
	required.ServeHTTP(recorder, httptest.NewRequest(http.MethodPost, "/v1/chat/completions", nil))
	if recorder.Code != http.StatusForbidden {
		t.Fatalf("missing scope status=%d", recorder.Code)
	}
	request := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", nil)
	request.Header.Set(AccountScopeHeader, "a@example.com, b@example.com")
	recorder = httptest.NewRecorder()
	required.ServeHTTP(recorder, request)
	if recorder.Code != http.StatusNoContent || !scoped || len(seen) != 2 || seen[1] != "b@example.com" {
		t.Fatalf("status=%d scope=%v scoped=%v", recorder.Code, seen, scoped)
	}
	request = httptest.NewRequest(http.MethodPost, "/v1/chat/completions", nil)
	request.Header.Set(AccountScopeHeader, "")
	recorder = httptest.NewRecorder()
	required.ServeHTTP(recorder, request)
	if recorder.Code != http.StatusNoContent || !scoped || len(seen) != 0 {
		t.Fatalf("empty scope must mean no accounts: status=%d scope=%v scoped=%v", recorder.Code, seen, scoped)
	}
	recorder = httptest.NewRecorder()
	accountScopeMiddleware(false, next).ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/v1/models", nil))
	if recorder.Code != http.StatusNoContent || scoped {
		t.Fatalf("optional mode status=%d scoped=%v", recorder.Code, scoped)
	}
}

func TestServingAccountHeader(t *testing.T) {
	handler := accountScopeMiddleware(true, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		aistudio.ReportServingAccount(r.Context(), "a@example.com")
		aistudio.ReportServingAccount(r.Context(), "b@example.com")
		w.Write([]byte("data: {}\n\n"))
		aistudio.ReportServingAccount(r.Context(), "late@example.com")
	}))
	request := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", nil)
	request.Header.Set(AccountScopeHeader, "a@example.com,b@example.com")
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, request)
	if got := recorder.Header().Get(ServingAccountHeader); got != "b@example.com" {
		t.Fatalf("serving account header=%q", got)
	}
}
