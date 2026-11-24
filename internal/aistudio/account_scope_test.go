package aistudio

import (
	"context"
	"errors"
	"testing"
	"time"
)

func scopeFixturePool(t *testing.T) *AccountPool {
	t.Helper()
	model := Model{ID: "gemini", Methods: []string{"generateContent"}, Capabilities: map[string]bool{"chat_model": true}}
	accounts := []*Account{
		{ID: "alice@example.com", Config: AccountConfig{Enabled: true}, State: AccountReady, Models: []Model{model}, modelAccessGeneration: 1},
		{ID: "alice2@example.com", Config: AccountConfig{Enabled: true}, State: AccountReady, Models: []Model{model}, modelAccessGeneration: 1},
		{ID: "bob@example.com", Config: AccountConfig{Enabled: true}, State: AccountReady, Models: []Model{model}, modelAccessGeneration: 1},
	}
	return NewAccountPool(accounts, 4)
}

func pickAccount(t *testing.T, pool *AccountPool, ctx context.Context, selection AccountSelection) (string, error) {
	t.Helper()
	lease, _, err := pool.TryAcquireFor(ctx, selection)
	if err != nil || lease == nil {
		return "", err
	}
	defer lease.Release()
	return lease.Account().ID, nil
}

// TestAccountScopeIsolatesUsers 验证共享引擎的可信账户范围：只在本人账户中选号，指定他人账户也会被拒绝
func TestAccountScopeIsolatesUsers(t *testing.T) {
	pool := scopeFixturePool(t)
	selection := AccountSelection{ModelID: "gemini", Method: "generateContent"}
	bob := ContextWithAccountScope(context.Background(), []string{"bob@example.com"})
	for range 5 {
		got, err := pickAccount(t, pool, bob, selection)
		if err != nil || got != "bob@example.com" {
			t.Fatalf("bob picked %q err=%v", got, err)
		}
	}
	explicit := selection
	explicit.AccountID = "alice@example.com"
	if got, err := pickAccount(t, pool, bob, explicit); got != "" || !errors.Is(err, ErrNoEligibleAccount) {
		t.Fatalf("explicit foreign account leaked: %q err=%v", got, err)
	}
	if got, err := pickAccount(t, pool, ContextWithAccount(bob, "alice@example.com"), selection); got != "" || !errors.Is(err, ErrNoEligibleAccount) {
		t.Fatalf("header-selected foreign account leaked: %q err=%v", got, err)
	}
	// 检查放宽 AllowedAccountIDs 的重试路径也会被范围重新收窄
	widened := selection
	widened.AllowedAccountIDs = []string{"alice@example.com", "alice2@example.com", "bob@example.com"}
	for range 5 {
		if got, _ := pickAccount(t, pool, bob, widened); got != "bob@example.com" {
			t.Fatalf("widened retry escaped scope: %q", got)
		}
	}
}

// TestEmptyAccountScopeSelectsNothing 验证空范围不等于不限范围
func TestEmptyAccountScopeSelectsNothing(t *testing.T) {
	pool := scopeFixturePool(t)
	ctx := ContextWithAccountScope(context.Background(), nil)
	if got, _ := pickAccount(t, pool, ctx, AccountSelection{ModelID: "gemini", Method: "generateContent"}); got != "" {
		t.Fatalf("empty scope picked %q", got)
	}
	scope, ok := ContextAccountScope(ctx)
	if !ok || scope == nil || len(scope) != 0 {
		t.Fatalf("scope=%v ok=%v", scope, ok)
	}
}

// TestAccountScopeSurvivesSelectionScopeCopy 验证后台续接操作沿用发起请求的账户范围
func TestAccountScopeSurvivesSelectionScopeCopy(t *testing.T) {
	source := ContextWithAccountScope(context.Background(), []string{" a@example.com ", "a@example.com", "b@example.com"})
	copied := ContextWithSelectionScope(context.Background(), source)
	scope, ok := ContextAccountScope(copied)
	if !ok || len(scope) != 2 || scope[0] != "a@example.com" || scope[1] != "b@example.com" {
		t.Fatalf("scope=%v ok=%v", scope, ok)
	}
}

// TestScopedCooldownSwitchesWithinOwnAccounts 验证 429 冷却后只切换到同一用户的其他账户
func TestScopedCooldownSwitchesWithinOwnAccounts(t *testing.T) {
	pool := scopeFixturePool(t)
	selection := AccountSelection{ModelID: "gemini", Method: "generateContent"}
	alice := ContextWithAccountScope(context.Background(), []string{"alice@example.com", "alice2@example.com"})
	now := time.Now()
	if err := pool.MarkCooldownIfGeneration("alice@example.com", "gemini", 1, now, now.Add(time.Hour), "fixture 429"); err != nil {
		t.Fatal(err)
	}
	for range 4 {
		got, err := pickAccount(t, pool, alice, selection)
		if err != nil || got != "alice2@example.com" {
			t.Fatalf("alice picked %q err=%v", got, err)
		}
	}
	if err := pool.MarkCooldownIfGeneration("alice2@example.com", "gemini", 1, now, now.Add(2*time.Hour), "fixture 429"); err != nil {
		t.Fatal(err)
	}
	if got, _ := pickAccount(t, pool, alice, selection); got != "" {
		t.Fatalf("all own accounts cooling but picked %q (bob must never be borrowed)", got)
	}
}

func TestCooldownQueueLimitFromEnv(t *testing.T) {
	for value, want := range map[string]time.Duration{"": time.Minute, "0": 0, "15": 15 * time.Second, "-1": time.Minute, "x": time.Minute, "601": time.Minute} {
		if got := cooldownQueueLimitFromEnv(value); got != want {
			t.Fatalf("%q => %v want %v", value, got, want)
		}
	}
}
